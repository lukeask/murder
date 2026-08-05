"""Shared helpers for built-in application handler modules."""

from __future__ import annotations

import asyncio
from typing import Any

from murder.app.protocol.read_models import dto_to_wire


def threaded(fn: Any) -> Any:
    """Offload a *synchronous*, thread-safe application handler to a worker
    thread so its blocking sqlite/git/file work does not starve the
    event loop. The broker awaits returned coroutines on the loop, so
    ``asyncio.to_thread`` runs ``fn`` off-loop and yields the dict. Only
    safe for handlers backed by ``ServiceReadModel`` (fresh per-call
    sqlite connection) or pure git/file reads — never a handler that
    touches the shared long-lived process ``db`` connection."""
    return lambda body=None: asyncio.to_thread(fn, body)


def value(value: Any) -> dict[str, Any]:
    return {"ok": True, "value": dto_to_wire(value)}
