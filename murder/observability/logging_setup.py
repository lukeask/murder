"""Central logging configuration (Step 1.1).

The daemon owns the root logger: stderr NDJSON (so the service child's
stdout/stderr ``daemon.ndjson`` keeps receiving output) and an optional
daemon-level file handler.

Per-repo run logs (``service.log``) attach as filtered handlers on the
``murder`` package logger — never as additional root handlers — keyed by
``repository_id`` via :class:`RepositoryIdFilter`. See
:func:`configure_repo_logging`.

:func:`configure_logging` is idempotent: calling it twice never double-adds the
stderr handler, but a later call that first supplies a ``log_path`` will attach
the (daemon) file handler. Handlers are tagged with a sentinel attribute to make
this cheap and robust.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from murder.observability.log_context import (
    CONTEXT_FIELDS,
    FixedFieldsFilter,
    LogContextFilter,
    RepositoryIdFilter,
)

LOG = logging.getLogger(__name__)

VALID_LEVELS: frozenset[str] = frozenset({"DEBUG", "INFO", "WARNING", "ERROR"})
DEFAULT_LEVEL = "INFO"

# The single verbosity ladder (plan §"The verbosity ladder — ONE knob"). This is
# the ONLY place that knows the ladder's shape: each rung of the user-facing
# ``--log-level`` knob maps to (python logging level, flight-recorder mode).
# ``advanced`` / ``advanced-raw`` are the TOP of the same ladder, not a second
# axis — there are no separate ``--advanced-logging`` flags. Nothing downstream
# branches on the raw string; callers ask for the python level OR the recorder
# mode via the two resolvers below.
LADDER: dict[str, tuple[str, str]] = {
    "error": ("ERROR", "off"),
    "warning": ("WARNING", "off"),
    "info": ("INFO", "off"),
    "debug": ("DEBUG", "off"),
    "advanced": ("DEBUG", "redacted"),
    "advanced-raw": ("DEBUG", "raw"),
}
DEFAULT_RUNG = "info"
# Accepted on the CLI / env / config; surfaced in --log-level help.
VALID_RUNGS: tuple[str, ...] = tuple(LADDER)

# Sentinel attributes tagging handlers this module installs, so repeat calls are
# idempotent and we never collide with handlers installed elsewhere.
_STDERR_TAG = "_murder_stderr_handler"
_FILE_TAG = "_murder_file_path"
_REPO_FILE_TAG = "_murder_repo_file_path"
_REPO_ID_TAG = "_murder_repository_id"
_REPO_LOGGER_PREFIX = "murder.repo."

# Standard LogRecord attributes we never emit as structured extras.
_RESERVED = frozenset(
    {
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread", "threadName",
        "processName", "process", "taskName", "message",
    }
)


class NdjsonFormatter(logging.Formatter):
    """Render each record as a single-line JSON object.

    Always emits ``ts`` (ISO8601 UTC), ``level``, ``logger``, ``msg``. Any of the
    correlation fields present on the record (set by :class:`LogContextFilter`)
    are included; absent ones are omitted. ``exc_info`` is rendered to traceback
    text under ``exc``.
    """

    def format(self, record: logging.LogRecord) -> str:
        obj: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for field in CONTEXT_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                obj[field] = value
        if record.exc_info:
            obj["exc"] = self.formatException(record.exc_info)
        elif record.exc_text:
            obj["exc"] = record.exc_text
        # Any structured extras attached via ``logger.info(..., extra={...})``.
        for key, value in record.__dict__.items():
            if key in _RESERVED or key in CONTEXT_FIELDS or key in obj:
                continue
            if key.startswith("_"):
                continue
            try:
                json.dumps(value)
            except (TypeError, ValueError):
                value = repr(value)
            obj[key] = value
        return json.dumps(obj, default=str)


def _normalize_level(level: str) -> str:
    candidate = (level or "").upper()
    return candidate if candidate in VALID_LEVELS else DEFAULT_LEVEL


def configure_logging(*, level: str, log_path: Path | None) -> None:
    """Configure the daemon-owned root logger with NDJSON output (idempotent).

    Always ensures a stderr handler exists; if ``log_path`` is given, ensures a
    single daemon :class:`logging.FileHandler` for that path is attached on the
    **root** logger. Per-repo run logs must use :func:`configure_repo_logging`
    instead — never pass a per-repo ``service.log`` here under a multi-host
    daemon.
    """
    root = logging.getLogger()
    normalized = _normalize_level(level)
    root.setLevel(normalized)

    formatter = NdjsonFormatter()
    context_filter = LogContextFilter()

    # Ensure exactly one tagged stderr handler.
    have_stderr = any(getattr(h, _STDERR_TAG, False) for h in root.handlers)
    if not have_stderr:
        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.setFormatter(formatter)
        stderr_handler.addFilter(context_filter)
        setattr(stderr_handler, _STDERR_TAG, True)
        root.addHandler(stderr_handler)

    # Ensure at most one daemon file handler per distinct path (root only).
    if log_path is not None:
        target = str(Path(log_path))
        have_file = any(getattr(h, _FILE_TAG, None) == target for h in root.handlers)
        if not have_file:
            os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
            file_handler = logging.FileHandler(target, encoding="utf-8")
            file_handler.setFormatter(formatter)
            file_handler.addFilter(context_filter)
            setattr(file_handler, _FILE_TAG, target)
            root.addHandler(file_handler)


def repo_logger_name(repository_id: str) -> str:
    """Return the child logger name for a repository host."""
    return f"{_REPO_LOGGER_PREFIX}{repository_id}"


def configure_repo_logging(
    *,
    repository_id: str,
    level: str,
    log_path: Path,
    run_id: str | None = None,
) -> logging.Logger:
    """Attach a per-repo run log without adding handlers to the root logger.

    The FileHandler lives on the ``murder`` package logger and is gated by
    :class:`RepositoryIdFilter` so concurrent hosts cannot cross-write. A named
    child logger ``murder.repo.{repository_id}`` is returned for explicit use.
    Idempotent for the same ``(repository_id, log_path)``.
    """
    normalized = _normalize_level(level)
    child = logging.getLogger(repo_logger_name(repository_id))
    child.setLevel(normalized)

    package = logging.getLogger("murder")
    # Ensure package level is at least as verbose as the repo rung so filtered
    # handlers still see DEBUG records when requested.
    if package.level == logging.NOTSET or package.level > getattr(logging, normalized):
        package.setLevel(normalized)

    target = str(Path(log_path))
    for handler in list(package.handlers):
        if getattr(handler, _REPO_ID_TAG, None) != repository_id:
            continue
        if getattr(handler, _REPO_FILE_TAG, None) == target:
            return child
        # Same repo, different run path (stale handler after incomplete teardown):
        # drop it so we never dual-write across runs.
        package.removeHandler(handler)
        handler.close()

    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    file_handler = logging.FileHandler(target, encoding="utf-8")
    file_handler.setFormatter(NdjsonFormatter())
    file_handler.addFilter(LogContextFilter())
    file_handler.addFilter(RepositoryIdFilter(repository_id))
    if run_id is not None:
        file_handler.addFilter(FixedFieldsFilter(run_id=run_id))
    setattr(file_handler, _REPO_FILE_TAG, target)
    setattr(file_handler, _REPO_ID_TAG, repository_id)
    package.addHandler(file_handler)
    return child


def close_repo_logging(repository_id: str) -> None:
    """Remove and close per-repo file handlers for ``repository_id``."""
    package = logging.getLogger("murder")
    for handler in list(package.handlers):
        if getattr(handler, _REPO_ID_TAG, None) != repository_id:
            continue
        package.removeHandler(handler)
        handler.close()


def _normalize_rung(value: str | None) -> str | None:
    """Map a raw value to a ladder rung, or ``None`` if it isn't one.

    Tolerant of case and ``advanced_raw`` / ``advanced-raw`` punctuation, and of
    the legacy upper-case python-level spellings (``INFO`` → ``info``)."""
    if not value:
        return None
    candidate = value.strip().lower().replace("_", "-")
    return candidate if candidate in LADDER else None


def resolve_rung(cli_value: str | None = None) -> str:
    """Resolve the effective ``--log-level`` rung (a key of :data:`LADDER`).

    Precedence: ``cli_value`` > ``MURDER_LOG_LEVEL`` env > user config
    ``log_level`` > ``info``. An unrecognised value at any tier is skipped so a
    typo falls through to the next tier rather than crashing. This is the single
    resolver the plan calls for; level and recorder mode are both derived here.
    """
    for candidate in (cli_value, os.environ.get("MURDER_LOG_LEVEL"), _config_log_level()):
        rung = _normalize_rung(candidate)
        if rung is not None:
            return rung
    return DEFAULT_RUNG


def level_for_rung(rung: str) -> str:
    """Map an already-resolved rung to its python logging level.

    The one place that knows :data:`LADDER`'s tuple shape, so callers that
    already hold a rung don't index ``[0]`` themselves.
    """
    return LADDER[rung][0]


def recorder_mode_for_rung(rung: str) -> str:
    """Map an already-resolved rung to its flight-recorder mode."""
    return LADDER[rung][1]


def resolve_log_level(cli_value: str | None = None) -> str:
    """Resolve the effective python logging level via the single ladder."""
    return level_for_rung(resolve_rung(cli_value))


def resolve_recorder_mode(cli_value: str | None = None) -> str:
    """Resolve the flight-recorder mode (``off`` / ``redacted`` / ``raw``).

    The recorder is the top of the ``--log-level`` ladder, not a second flag:
    ``advanced`` → ``redacted``, ``advanced-raw`` → ``raw``, everything below →
    ``off``.
    """
    return recorder_mode_for_rung(resolve_rung(cli_value))


def _config_log_level() -> Optional[str]:
    """Read ``log_level`` from user config, swallowing any load failure."""
    try:
        from murder.user_config import load_user_config

        return getattr(load_user_config(), "log_level", None)
    except Exception:  # pragma: no cover - config load must never crash logging
        LOG.debug("could not read log_level from user config", exc_info=True)
        return None
