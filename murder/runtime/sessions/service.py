"""Process-owned session store, controllers, and terminal output readers."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import AsyncIterator
from uuid import UUID

from murder.permissions.persistence import PermissionStore
from murder.permissions.policy import LocalServicePermissionPolicy
from murder.permissions.service import PermissionService
from murder.permissions.session import SessionPermissionAuthorizer
from murder.runtime.sessions.backend import SessionBackend, TmuxSessionBackend
from murder.runtime.sessions.contracts import (
    HarnessSessionRecord,
    SessionCapabilities,
    SessionStatus,
    SessionTransport,
)
from murder.runtime.sessions.controller import SessionController
from murder.runtime.sessions.persistence import SessionStore
from murder.runtime.sessions.registry import BackendFactory, SessionControllerRegistry
from murder.runtime.terminal.capture import CapturedTerminalFrame, capture_tmux_frame
from murder.runtime.terminal.output import TerminalOutputRegistry, TmuxTerminalOutput
from murder.state.persistence.connection import RepoDb


class SessionBackendKind(str, Enum):
    PLAIN_TMUX = "plain_tmux"
    VERIFIED_HARNESS = "verified_harness"


class SessionIdentityConflictError(RuntimeError):
    """Persisted session identity conflicts with the requested registration."""


@dataclass(frozen=True)
class TmuxSessionRegistration:
    session_id: UUID
    session_kind: str
    tmux_name: str
    capabilities: SessionCapabilities
    backend: SessionBackendKind
    status: SessionStatus = SessionStatus.READY
    agent_id: UUID | None = None
    model: str | None = None
    effort: str | None = None
    owning_workflow_id: UUID | None = None
    owning_activity_id: UUID | None = None


_REVIVABLE = frozenset(
    {
        SessionStatus.STOPPING,
        SessionStatus.STOPPED,
        SessionStatus.FAILED,
        SessionStatus.LOST,
    }
)


def persist_or_revive_tmux_session(
    store: SessionStore,
    *,
    repository_id: UUID,
    registration: TmuxSessionRegistration,
) -> HarnessSessionRecord:
    """Create or revive a TMUX harness_sessions row (single revival algorithm)."""

    existing = store.get_session(registration.session_id)
    now = datetime.now(timezone.utc)

    if existing is None:
        record = HarnessSessionRecord(
            session_id=registration.session_id,
            agent_id=registration.agent_id,
            repository_id=repository_id,
            harness=registration.session_kind,
            model=registration.model,
            effort=registration.effort,
            transport=SessionTransport.TMUX,
            transport_ref=registration.tmux_name,
            status=registration.status,
            revision=0,
            capabilities=registration.capabilities,
            owning_workflow_id=registration.owning_workflow_id,
            owning_activity_id=registration.owning_activity_id,
            started_at=now,
            last_observed_at=now,
        )
        store.save_session(record)
        return record

    if existing.harness != registration.session_kind:
        raise SessionIdentityConflictError("session kind conflicts with persisted session")
    if existing.transport is not SessionTransport.TMUX:
        raise SessionIdentityConflictError("persisted session does not use tmux transport")
    if existing.repository_id != repository_id:
        raise SessionIdentityConflictError(
            "session repository conflicts with persisted session"
        )
    if existing.status in _REVIVABLE:
        record = existing.model_copy(
            update={
                "transport_ref": registration.tmux_name,
                "status": SessionStatus.READY,
                "revision": existing.revision + 1,
                "stopped_at": None,
                "last_observed_at": now,
                "capabilities": registration.capabilities,
                "model": registration.model
                if registration.model is not None
                else existing.model,
                "effort": registration.effort
                if registration.effort is not None
                else existing.effort,
                "agent_id": registration.agent_id
                if registration.agent_id is not None
                else existing.agent_id,
            }
        )
        store.save_session(record, expected_revision=existing.revision)
        return record

    if existing.transport_ref != registration.tmux_name:
        raise SessionIdentityConflictError(
            "live session transport_ref conflicts with registration"
        )
    return existing


def _permission_controller_factory(db: RepoDb, store: SessionStore):
    permission_service = PermissionService(
        store=PermissionStore(db),
        policy=LocalServicePermissionPolicy(),
    )
    permission_authorizer = SessionPermissionAuthorizer(permission_service)

    def controller_factory(
        record: HarnessSessionRecord,
        backend: SessionBackend,
    ) -> SessionController:
        return SessionController(
            record=record,
            store=store,
            backend=backend,
            authorizer=permission_authorizer,
            takeover_authorizer=lambda request, holder, current_lease, proof: (
                permission_authorizer.authorize_takeover(
                    request,
                    holder=holder,
                    current_lease=current_lease,
                    authorization=proof,
                )
            ),
        )

    return controller_factory


def _default_tmux_backend_factory(record: HarnessSessionRecord) -> SessionBackend:
    if record.transport is not SessionTransport.TMUX:
        raise RuntimeError(
            f"unsupported session transport {record.transport!r} for automatic backend"
        )
    return TmuxSessionBackend(record.transport_ref)


class SessionService:
    """Narrow process owner for session persistence, controllers, and output."""

    def __init__(
        self,
        db: RepoDb,
        *,
        backend_factory: BackendFactory | None = None,
    ) -> None:
        self._db = db
        self._store = SessionStore(db)
        self._controllers = SessionControllerRegistry(
            store=self._store,
            backend_factory=backend_factory or _default_tmux_backend_factory,
            controller_factory=_permission_controller_factory(db, self._store),
        )
        self._outputs = TerminalOutputRegistry()
        self._closed = False

    @classmethod
    @asynccontextmanager
    async def open(
        cls,
        db: RepoDb,
        *,
        backend_factory: BackendFactory | None = None,
    ) -> AsyncIterator[SessionService]:
        service = cls(db, backend_factory=backend_factory)
        try:
            yield service
        finally:
            await service.close()

    @property
    def store(self) -> SessionStore:
        return self._store

    @property
    def controllers(self) -> SessionControllerRegistry:
        return self._controllers

    @property
    def outputs(self) -> TerminalOutputRegistry:
        return self._outputs

    async def ensure_persisted_tmux_session(
        self,
        registration: TmuxSessionRegistration,
        *,
        session_backend: SessionBackend | None = None,
        recover: bool = False,
    ) -> SessionController:
        """Create or revive a TMUX session record and return its controller.

        Unifies the former harness and document-editor revival paths: STOPPING
        (and other terminal statuses) revive to READY with a refreshed
        ``transport_ref``.
        """

        if self._closed:
            raise RuntimeError("SessionService is closed")
        backend = self._resolve_backend(registration, session_backend)
        prior = self._store.get_session(registration.session_id)
        record = persist_or_revive_tmux_session(
            self._store,
            repository_id=UUID(self._db.repository_id),
            registration=registration,
        )
        # Revival refreshes transport_ref / status in the store. Drop any live
        # controller and output reader so get_or_create / open install backends
        # and control clients bound to the new ref instead of retaining stale ones.
        if prior is not None and prior.status in _REVIVABLE:
            await self._controllers.remove(record.session_id)
            await self._outputs.remove(record.session_id)
        return await self._controllers.get_or_create(
            record,
            backend=backend,
            recover=recover,
        )

    def resolve_tmux_ref(self, session_id: UUID) -> str:
        record = self._store.get_session(session_id)
        if record is None:
            raise ValueError(f"persisted session {session_id} does not exist")
        if record.transport is not SessionTransport.TMUX:
            raise ValueError(f"session {session_id} does not expose a tmux terminal")
        return record.transport_ref

    async def capture_terminal(self, session_id: UUID) -> CapturedTerminalFrame:
        if self._closed:
            raise RuntimeError("SessionService is closed")
        return await capture_tmux_frame(self.resolve_tmux_ref(session_id))

    async def open_terminal_output(self, session_id: UUID) -> TmuxTerminalOutput:
        if self._closed:
            raise RuntimeError("SessionService is closed")
        return await self._outputs.open(
            session_id,
            tmux_name=self.resolve_tmux_ref(session_id),
        )

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._outputs.close()
        await self._controllers.close()

    def _resolve_backend(
        self,
        registration: TmuxSessionRegistration,
        session_backend: SessionBackend | None,
    ) -> SessionBackend:
        if session_backend is not None:
            return session_backend
        if registration.backend is SessionBackendKind.PLAIN_TMUX:
            return TmuxSessionBackend(registration.tmux_name)
        raise ValueError(
            "verified harness registration requires an explicit session_backend"
        )


__all__ = [
    "SessionBackendKind",
    "SessionIdentityConflictError",
    "SessionService",
    "TmuxSessionRegistration",
    "persist_or_revive_tmux_session",
]
