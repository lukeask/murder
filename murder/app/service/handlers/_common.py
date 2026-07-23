"""Shared helpers for the built-in RPC handler modules."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from murder.app.protocol.read_models import dto_to_wire
from murder.app.service.read_model import ServiceReadModel
from murder.runtime.orchestration.orchestrator import Orchestrator

if TYPE_CHECKING:
    from murder.app.service.host import ServiceHost


def require_read_model(host: ServiceHost) -> ServiceReadModel:
    if host.read_model is None:
        raise RuntimeError("read model unavailable")
    return host.read_model


def require_orchestrator(host: ServiceHost) -> Orchestrator:
    if host.orchestrator is None:
        raise RuntimeError("orchestrator unavailable")
    return host.orchestrator


def threaded(fn: Any) -> Any:
    """Offload a *synchronous*, thread-safe RPC handler to a worker
    thread so its blocking sqlite/git/file work does not starve the
    event loop. The broker awaits returned coroutines on the loop, so
    ``asyncio.to_thread`` runs ``fn`` off-loop and yields the dict. Only
    safe for handlers backed by ``ServiceReadModel`` (fresh per-call
    sqlite connection) or pure git/file reads — never a handler that
    touches the shared long-lived ``runtime.db`` connection."""
    return lambda body=None: asyncio.to_thread(fn, body)


def value(value: Any) -> dict[str, Any]:
    return {"ok": True, "value": dto_to_wire(value)}
