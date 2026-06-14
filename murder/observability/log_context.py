"""Ambient correlation context for structured logging (Step 1.2).

Holds the four correlation ids (``run_id`` / ``agent_id`` / ``command_id`` /
``event_id``) in :mod:`contextvars` so they flow across ``await`` boundaries and
into log records without threading them through every call. The companion
:class:`LogContextFilter` copies whatever is currently set onto each
:class:`logging.LogRecord`; the NDJSON formatter in :mod:`logging_setup` reads
the same attribute names.

These four ids are exactly the central boundaries Phase 2 reuses, so they are
instrumented once here and at the dispatch / bus / agent choke points.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator, Optional

# The correlation fields the formatter knows how to emit. Order is irrelevant.
CONTEXT_FIELDS: tuple[str, ...] = ("run_id", "agent_id", "command_id", "event_id")

_run_id: ContextVar[Optional[str]] = ContextVar("run_id", default=None)
_agent_id: ContextVar[Optional[str]] = ContextVar("agent_id", default=None)
_command_id: ContextVar[Optional[str]] = ContextVar("command_id", default=None)
_event_id: ContextVar[Optional[str]] = ContextVar("event_id", default=None)

_VARS: dict[str, ContextVar[Optional[str]]] = {
    "run_id": _run_id,
    "agent_id": _agent_id,
    "command_id": _command_id,
    "event_id": _event_id,
}


class LogContextFilter(logging.Filter):
    """Copy the currently-set correlation contextvars onto each record.

    Only fields with a non-``None`` value are attached, so the formatter can
    omit absent ids instead of emitting ``null`` noise. Always returns ``True``
    (a filter, not a gate).
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003 - logging API
        for name, var in _VARS.items():
            value = var.get()
            if value is not None:
                setattr(record, name, value)
        return True


@contextmanager
def log_context(**fields: Optional[str]) -> Iterator[None]:
    """Temporarily set the given correlation contextvars, resetting on exit.

    Accepts any of ``run_id`` / ``agent_id`` / ``command_id`` / ``event_id``;
    unknown keys are ignored. Re-entrant and cheap.
    """
    tokens = []
    for name, value in fields.items():
        var = _VARS.get(name)
        if var is None:
            continue
        tokens.append((var, var.set(value)))
    try:
        yield
    finally:
        # Reset in reverse so nested same-key contexts unwind correctly.
        for var, token in reversed(tokens):
            var.reset(token)


def set_run_id(run_id: str) -> None:
    """Set the process-wide ``run_id`` (no reset).

    The service process owns exactly one run, so this is set once at
    ``Runtime.start`` and never unwound.
    """
    _run_id.set(run_id)
