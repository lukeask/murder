"""Ambient correlation context for structured logging (Step 1.2).

Holds correlation ids (``run_id`` / ``agent_id`` / ``command_id`` / ``event_id``)
and the multi-repo ``repository_id`` in :mod:`contextvars` so they flow across
``await`` boundaries. The companion :class:`LogContextFilter` copies whatever
correlation ids are currently set onto each :class:`logging.LogRecord`; the
NDJSON formatter in :mod:`logging_setup` reads the same attribute names.

``repository_id`` is host-scoped (not emitted as a correlation field by default):
per-repo file handlers filter on it so a single-daemon process never mixes run
logs across hosts. Prefer binding these via a per-host ``contextvars.Context``
(see ``ProcessScope.observability_context``) rather than process-global sets.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine, Iterator
from contextlib import contextmanager
from contextvars import Context, ContextVar
from typing import Any, Optional, TypeVar

_T = TypeVar("_T")

# The correlation fields the formatter knows how to emit. Order is irrelevant.
CONTEXT_FIELDS: tuple[str, ...] = ("run_id", "agent_id", "command_id", "event_id")

_run_id: ContextVar[Optional[str]] = ContextVar("run_id", default=None)
_agent_id: ContextVar[Optional[str]] = ContextVar("agent_id", default=None)
_command_id: ContextVar[Optional[str]] = ContextVar("command_id", default=None)
_event_id: ContextVar[Optional[str]] = ContextVar("event_id", default=None)
_repository_id: ContextVar[Optional[str]] = ContextVar("repository_id", default=None)

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


class RepositoryIdFilter(logging.Filter):
    """Admit records only when the ambient ``repository_id`` matches.

    Used by per-repo file handlers attached to the ``murder`` package logger so
    multi-host daemons do not cross-write run logs.
    """

    def __init__(self, repository_id: str) -> None:
        super().__init__()
        self._repository_id = repository_id

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003 - logging API
        return _repository_id.get() == self._repository_id


class FixedFieldsFilter(logging.Filter):
    """Stamp fixed fields onto every record (e.g. host ``run_id``) when unset."""

    def __init__(self, **fields: str) -> None:
        super().__init__()
        self._fields = fields

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003 - logging API
        for name, value in self._fields.items():
            if getattr(record, name, None) is None:
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
    """Bind ``run_id`` in the *current* context (no reset).

    Prefer setting this inside a per-host ``contextvars.Context`` rather than
    the daemon's ambient task context, so concurrent RepositoryHosts do not
    clobber each other.
    """
    _run_id.set(run_id)


def set_repository_id(repository_id: str) -> None:
    """Bind ``repository_id`` in the *current* context (no reset).

    Used by :class:`RepositoryIdFilter` on per-repo run-log handlers.
    """
    _repository_id.set(repository_id)


def get_repository_id() -> Optional[str]:
    """Return the ambient repository id, if any."""
    return _repository_id.get()


def create_task_with_context(
    coro: Coroutine[Any, Any, _T],
    *,
    name: str | None = None,
    context: Context | None = None,
) -> asyncio.Task[_T]:
    """Like ``asyncio.create_task``, optionally bound to a host ``Context``.

    ``asyncio.create_task(..., context=)`` exists only on Python 3.11+. Murder
    supports 3.10+, so when ``context`` is set we spawn the task from inside
    ``context.run`` — ``create_task`` then copies the current context into the
    new Task on every supported interpreter.
    """
    if context is None:
        return asyncio.create_task(coro, name=name)

    def _spawn() -> asyncio.Task[_T]:
        return asyncio.create_task(coro, name=name)

    return context.run(_spawn)


def adopt_context_ids(host_context: Context) -> None:
    """Copy ``run_id`` / ``repository_id`` from ``host_context`` into this task.

    Used by long-lived aiohttp handlers that are not themselves spawned under
    the host observability context, so non-stream work (and child tasks that
    inherit) still hit per-repo log filters.
    """

    def _read() -> tuple[str | None, str | None]:
        return _run_id.get(), _repository_id.get()

    run_id, repository_id = host_context.run(_read)
    if run_id is not None:
        _run_id.set(run_id)
    if repository_id is not None:
        _repository_id.set(repository_id)


def adopt_observability_context(host_context: Context) -> None:
    """Install host ``run_id`` / ``repository_id`` / advanced-log into this task."""
    from murder.observability.advanced_log import (  # noqa: PLC0415
        current_advanced_log,
        set_current_advanced_log,
    )

    adopt_context_ids(host_context)
    set_current_advanced_log(host_context.run(current_advanced_log))
