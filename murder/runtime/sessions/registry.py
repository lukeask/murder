"""Registry guaranteeing exactly one controller for each persisted session."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Awaitable, Callable
from uuid import UUID

from murder.permissions.persistence import PermissionStore
from murder.permissions.policy import LocalServicePermissionPolicy
from murder.permissions.service import PermissionService
from murder.permissions.session import SessionPermissionAuthorizer
from murder.runtime.sessions.backend import SessionBackend
from murder.runtime.sessions.contracts import HarnessSessionRecord
from murder.runtime.sessions.controller import (
    SessionController,
    trusted_local_session_authorizer,
)
from murder.runtime.sessions.persistence import SessionNotFoundError, SessionStore

BackendFactory = Callable[
    [HarnessSessionRecord],
    SessionBackend | Awaitable[SessionBackend],
]
ControllerFactory = Callable[[HarnessSessionRecord, SessionBackend], SessionController]


class SessionBackendRequiredError(RuntimeError):
    pass


class SessionControllerRegistry:
    """The service-level owner of controller identity and restart recovery."""

    def __init__(
        self,
        *,
        store: SessionStore,
        backend_factory: BackendFactory | None = None,
        controller_factory: ControllerFactory | None = None,
    ) -> None:
        self._store = store
        self._backend_factory = backend_factory
        # Default denies until an explicit authorizer is supplied. Production
        # uses ``registry_for_connection`` (SessionPermissionAuthorizer);
        # tests that need the old bypass must opt into
        # ``trusted_local_controller_factory``.
        self._controller_factory = controller_factory or (
            lambda record, backend: SessionController(
                record=record,
                store=self._store,
                backend=backend,
            )
        )
        self._controllers: dict[UUID, SessionController] = {}
        self._recovered: set[UUID] = set()
        self._lock = asyncio.Lock()

    async def get_or_create(
        self,
        session: UUID | HarnessSessionRecord,
        *,
        backend: SessionBackend | None = None,
        recover: bool = False,
    ) -> SessionController:
        record = (
            session
            if isinstance(session, HarnessSessionRecord)
            else self._store.get_session(session)
        )
        if record is None:
            raise SessionNotFoundError(f"session {session} does not exist")
        async with self._lock:
            existing = self._controllers.get(record.session_id)
            if existing is not None and existing.closed:
                del self._controllers[record.session_id]
                self._recovered.discard(record.session_id)
                existing = None
            controller = existing
            if controller is None:
                selected_backend = backend
                if selected_backend is None:
                    if self._backend_factory is None:
                        raise SessionBackendRequiredError(
                            "session controller requires a backend"
                        )
                    backend_or_awaitable = self._backend_factory(record)
                    if isinstance(backend_or_awaitable, Awaitable):
                        selected_backend = await backend_or_awaitable
                    else:
                        selected_backend = backend_or_awaitable
                controller = self._controller_factory(record, selected_backend)
                self._controllers[record.session_id] = controller
            if not recover or record.session_id in self._recovered:
                return controller
            try:
                await controller.recover()
            except BaseException:
                if self._controllers.get(record.session_id) is controller:
                    del self._controllers[record.session_id]
                await controller.close()
                raise
            self._recovered.add(record.session_id)
            return controller

    async def recover_persisted(self) -> tuple[SessionController, ...]:
        controllers = []
        for record in self._store.list_recoverable_sessions():
            controllers.append(await self.get_or_create(record, recover=True))
        return tuple(controllers)

    async def remove(self, session_id: UUID) -> None:
        async with self._lock:
            controller = self._controllers.pop(session_id, None)
            self._recovered.discard(session_id)
        if controller is not None:
            await controller.close()

    async def close(self) -> None:
        async with self._lock:
            controllers = tuple(self._controllers.values())
            self._controllers.clear()
            self._recovered.clear()
        await asyncio.gather(*(controller.close() for controller in controllers))

    @staticmethod
    def trusted_local_controller_factory(
        store: SessionStore,
    ) -> ControllerFactory:
        return lambda record, backend: SessionController(
            record=record,
            store=store,
            backend=backend,
            authorizer=trusted_local_session_authorizer,
        )


_CONNECTION_REGISTRIES: dict[int, tuple[sqlite3.Connection, SessionControllerRegistry]] = {}


def registry_for_connection(connection: sqlite3.Connection) -> SessionControllerRegistry:
    """Return the process owner for every controller persisted by this DB."""

    key = id(connection)
    current = _CONNECTION_REGISTRIES.get(key)
    if current is not None and current[0] is connection:
        return current[1]
    store = SessionStore(connection)
    permission_service = PermissionService(
        store=PermissionStore(connection),
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

    registry = SessionControllerRegistry(
        store=store,
        controller_factory=controller_factory,
    )
    _CONNECTION_REGISTRIES[key] = (connection, registry)
    return registry


async def close_registry_for_connection(connection: sqlite3.Connection) -> None:
    current = _CONNECTION_REGISTRIES.pop(id(connection), None)
    if current is not None and current[0] is connection:
        await current[1].close()


__all__ = [
    "BackendFactory",
    "SessionControllerRegistry",
    "close_registry_for_connection",
    "registry_for_connection",
]
