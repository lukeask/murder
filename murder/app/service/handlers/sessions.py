"""Typed session writer-lease and session-command handlers."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID

from murder.app.protocol.requests import CommandName, QueryName
from murder.app.protocol.sessions import (
    AcquireWriterLeaseParams,
    ExecuteSessionCommandParams,
    ExecuteSessionCommandResult,
    GetWriterLeaseParams,
    GetWriterLeaseResult,
    ReleaseWriterLeaseParams,
    RenewWriterLeaseParams,
)
from murder.app.service.application import ApplicationRegistrar
from murder.app.service.projection_registry import ProjectionProviderRegistry
from murder.contracts.common import request_context
from murder.runtime.sessions.contracts import (
    AcquireWriterLease,
    ReleaseWriterLease,
    RenewWriterLease,
    RequestMeta,
    WriterLeaseReply,
)
from murder.runtime.sessions.persistence import SessionNotFoundError, SessionStore
from murder.runtime.sessions.registry import (
    SessionBackendRequiredError,
    SessionControllerRegistry,
)

if TYPE_CHECKING:
    from murder.runtime.sessions.controller import SessionController


class SessionEffects(Protocol):
    """Runtime capabilities required by the session application feature."""

    db: sqlite3.Connection | None
    session_controllers: SessionControllerRegistry | None


def _meta(
    request_id: UUID | None,
    *,
    expected_revision: int | None = None,
) -> RequestMeta:
    return request_context(
        explicit_request_id=request_id,
        expected_revision=expected_revision,
    )


def _reply_json(reply: WriterLeaseReply) -> dict[str, object]:
    return reply.model_dump(mode="json")


def register(
    app: ApplicationRegistrar,
    projections: ProjectionProviderRegistry,
    effects: SessionEffects,
) -> None:
    """Register session use cases without coupling them to the service host.

    There is no session-list application read model yet, so this feature
    intentionally owns no ``sessions`` snapshot provider.  Registering an
    empty provider here would make the subscription look authoritative when it
    is not.
    """
    del projections

    def _registry() -> SessionControllerRegistry:
        registry = getattr(effects, "session_controllers", None)
        if not isinstance(registry, SessionControllerRegistry):
            raise RuntimeError("session controller registry is unavailable")
        return registry

    def _store() -> SessionStore:
        connection = getattr(effects, "db", None)
        if connection is None:
            raise RuntimeError("service not started")
        return SessionStore(connection)

    async def _controller(session_id: UUID) -> SessionController:
        registry = _registry()
        try:
            return await registry.get_or_create(session_id)
        except SessionNotFoundError:
            raise
        except SessionBackendRequiredError as exc:
            raise RuntimeError(
                f"session {session_id} has no live controller"
            ) from exc

    def _get(body: dict[str, object]) -> dict[str, object]:
        params = GetWriterLeaseParams.model_validate(body)
        store = _store()
        if store.get_session(params.session_id) is None:
            return GetWriterLeaseResult(ok=False, error="not_found").model_dump(mode="json")
        lease = store.active_writer_lease(params.session_id)
        return GetWriterLeaseResult(ok=True, lease=lease).model_dump(mode="json")

    async def _acquire(body: dict[str, object]) -> dict[str, object]:
        params = AcquireWriterLeaseParams.model_validate(body)
        if params.holder is None:
            raise ValueError("session.writer.acquire requires a holder")
        controller = await _controller(params.session_id)
        reply = await controller.acquire_writer_lease(
            AcquireWriterLease(
                meta=_meta(
                    params.request_id,
                    expected_revision=params.expected_revision,
                ),
                session_id=params.session_id,
                mode=params.mode,
                ttl_seconds=params.ttl_seconds,
                force=params.force,
            ),
            holder=params.holder,
        )
        return _reply_json(reply)

    async def _renew(body: dict[str, object]) -> dict[str, object]:
        params = RenewWriterLeaseParams.model_validate(body)
        if params.holder is None:
            raise ValueError("session.writer.renew requires a holder")
        controller = await _controller(params.session_id)
        reply = await controller.renew_writer_lease(
            RenewWriterLease(
                meta=_meta(
                    params.request_id,
                    expected_revision=params.expected_revision,
                ),
                lease_id=params.lease_id,
                fence=params.fence,
                ttl_seconds=params.ttl_seconds,
            ),
            holder=params.holder,
        )
        return _reply_json(reply)

    async def _release(body: dict[str, object]) -> dict[str, object]:
        params = ReleaseWriterLeaseParams.model_validate(body)
        if params.holder is None:
            raise ValueError("session.writer.release requires a holder")
        controller = await _controller(params.session_id)
        kwargs: dict[str, Any] = {}
        if params.reason is not None:
            kwargs["reason"] = params.reason
        reply = await controller.release_writer_lease(
            ReleaseWriterLease(
                meta=_meta(
                    params.request_id,
                    expected_revision=params.expected_revision,
                ),
                lease_id=params.lease_id,
                fence=params.fence,
            ),
            holder=params.holder,
            **kwargs,
        )
        return _reply_json(reply)

    async def _execute(body: dict[str, object]) -> dict[str, object]:
        params = ExecuteSessionCommandParams.model_validate(body)
        if params.principal is None:
            raise ValueError("session.command.execute requires a principal")
        controller = await _controller(params.session_id)
        receipt = await controller.execute(
            params.command,
            principal=params.principal,
            expected_revision=params.expected_revision,
            authorization=params.authorization,
        )
        return ExecuteSessionCommandResult(receipt=receipt).model_dump(mode="json")

    app.register_application_query(QueryName.SESSION_WRITER_GET, _get)
    app.register_application_command(CommandName.SESSION_WRITER_ACQUIRE, _acquire)
    app.register_application_command(CommandName.SESSION_WRITER_RENEW, _renew)
    app.register_application_command(CommandName.SESSION_WRITER_RELEASE, _release)
    app.register_application_command(CommandName.SESSION_COMMAND_EXECUTE, _execute)


__all__ = [
    "SessionEffects",
    "register",
]
