"""Typed session writer-lease and session-command handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from murder.app.protocol.sessions import (
    AcquireWriterLeaseParams,
    ExecuteSessionCommandParams,
    ExecuteSessionCommandResult,
    GetWriterLeaseParams,
    GetWriterLeaseResult,
    ReleaseWriterLeaseParams,
    RenewWriterLeaseParams,
)
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
    from murder.app.service.host import ServiceHost
    from murder.runtime.sessions.controller import SessionController


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


def register(host: ServiceHost) -> None:
    def _registry() -> SessionControllerRegistry:
        runtime = host.runtime
        registry = getattr(runtime, "session_controllers", None) if runtime is not None else None
        if not isinstance(registry, SessionControllerRegistry):
            raise RuntimeError("session controller registry is unavailable")
        return registry

    def _store() -> SessionStore:
        runtime = host.runtime
        if runtime is None or runtime.db is None:
            raise RuntimeError("service not started")
        return SessionStore(runtime.db)

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

    host.register_rpc_handler("session.writer.get", _get)
    host.register_rpc_handler("session.writer.acquire", _acquire)
    host.register_rpc_handler("session.writer.renew", _renew)
    host.register_rpc_handler("session.writer.release", _release)
    host.register_rpc_handler("session.command.execute", _execute)


__all__ = [
    "register",
]
