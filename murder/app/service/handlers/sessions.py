"""Typed session writer-lease query and command handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from murder.runtime.sessions.contracts import (
    AcquireWriterLease,
    Correlation,
    PrincipalRef,
    ReleaseWriterLease,
    RenewWriterLease,
    RequestMeta,
    WriterLeaseReply,
    WriterMode,
)
from murder.runtime.sessions.persistence import SessionNotFoundError, SessionStore
from murder.runtime.sessions.registry import (
    SessionBackendRequiredError,
    SessionControllerRegistry,
)

if TYPE_CHECKING:
    from murder.app.service.host import ServiceHost
    from murder.runtime.sessions.controller import SessionController


class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GetWriterLeaseParams(_Params):
    session_id: UUID


class AcquireWriterLeaseParams(_Params):
    session_id: UUID
    mode: WriterMode
    ttl_seconds: int = Field(default=15, ge=3, le=300)
    force: bool = False
    request_id: UUID | None = None
    expected_revision: int | None = None
    holder: PrincipalRef


class RenewWriterLeaseParams(_Params):
    session_id: UUID
    lease_id: UUID
    fence: int = Field(ge=1)
    ttl_seconds: int = Field(default=15, ge=3, le=300)
    request_id: UUID | None = None
    expected_revision: int | None = None
    holder: PrincipalRef


class ReleaseWriterLeaseParams(_Params):
    session_id: UUID
    lease_id: UUID
    fence: int = Field(ge=1)
    request_id: UUID | None = None
    expected_revision: int | None = None
    holder: PrincipalRef
    reason: str | None = None


def _request_meta(
    request_id: UUID | None,
    *,
    expected_revision: int | None = None,
) -> RequestMeta:
    rid = request_id or uuid4()
    return RequestMeta(
        request_id=rid,
        correlation=Correlation(correlation_id=rid),
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
            return {"ok": False, "error": "not_found"}
        lease = store.active_writer_lease(params.session_id)
        return {
            "ok": True,
            "lease": None if lease is None else lease.model_dump(mode="json"),
        }

    async def _acquire(body: dict[str, object]) -> dict[str, object]:
        params = AcquireWriterLeaseParams.model_validate(body)
        controller = await _controller(params.session_id)
        reply = await controller.acquire_writer_lease(
            AcquireWriterLease(
                meta=_request_meta(
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
        controller = await _controller(params.session_id)
        reply = await controller.renew_writer_lease(
            RenewWriterLease(
                meta=_request_meta(
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
        controller = await _controller(params.session_id)
        kwargs: dict[str, Any] = {}
        if params.reason is not None:
            kwargs["reason"] = params.reason
        reply = await controller.release_writer_lease(
            ReleaseWriterLease(
                meta=_request_meta(
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

    host.register_rpc_handler("session.writer.get", _get)
    host.register_rpc_handler("session.writer.acquire", _acquire)
    host.register_rpc_handler("session.writer.renew", _renew)
    host.register_rpc_handler("session.writer.release", _release)


__all__ = [
    "AcquireWriterLeaseParams",
    "GetWriterLeaseParams",
    "ReleaseWriterLeaseParams",
    "RenewWriterLeaseParams",
    "register",
]
