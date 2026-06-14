"""Central logging configuration (Step 1.1).

Installs a structured NDJSON formatter (one JSON object per line) on the root
logger, always keeping a stderr handler (so the service child's stdout/stderr
``supervisor.ndjson`` keeps receiving output) and optionally adding a per-run
``service.log`` file handler.

:func:`configure_logging` is idempotent: calling it twice never double-adds the
stderr handler, but a later call that first supplies a ``log_path`` will attach
the file handler. Handlers are tagged with a sentinel attribute to make this
cheap and robust.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from murder.observability.log_context import CONTEXT_FIELDS, LogContextFilter

LOG = logging.getLogger(__name__)

VALID_LEVELS: frozenset[str] = frozenset({"DEBUG", "INFO", "WARNING", "ERROR"})
DEFAULT_LEVEL = "INFO"

# Sentinel attributes tagging handlers this module installs, so repeat calls are
# idempotent and we never collide with handlers installed elsewhere.
_STDERR_TAG = "_murder_stderr_handler"
_FILE_TAG = "_murder_file_path"

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
    """Configure the root logger with NDJSON output (idempotent).

    Always ensures a stderr handler exists; if ``log_path`` is given, ensures a
    single :class:`logging.FileHandler` for that path is attached. Sets the root
    level from ``level`` (falling back to INFO on an invalid value).
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

    # Ensure at most one file handler per distinct path.
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


def resolve_log_level(cli_value: str | None = None) -> str:
    """Resolve the effective log level.

    Precedence: ``cli_value`` > ``MURDER_LOG_LEVEL`` env > user config
    ``log_level`` > ``INFO``. An invalid value at any tier falls back to INFO
    rather than crashing.
    """
    if cli_value:
        return _normalize_level(cli_value)

    env_value = os.environ.get("MURDER_LOG_LEVEL")
    if env_value:
        return _normalize_level(env_value)

    config_value = _config_log_level()
    if config_value:
        return _normalize_level(config_value)

    return DEFAULT_LEVEL


def _config_log_level() -> Optional[str]:
    """Read ``log_level`` from user config, swallowing any load failure."""
    try:
        from murder.user_config import load_user_config

        return getattr(load_user_config(), "log_level", None)
    except Exception:  # pragma: no cover - config load must never crash logging
        LOG.debug("could not read log_level from user config", exc_info=True)
        return None
