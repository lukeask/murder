"""Unit tests for session.writer.* application-protocol handlers."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from murder.app.service.handlers import sessions as sessions_handlers
from murder.app.service.projection_registry import ProjectionProviderRegistry
from murder.runtime.sessions.contracts import (
    AcquireWriterLease,
    Correlation,
    HarnessSessionRecord,
    PrincipalKind,
    PrincipalRef,
    RequestMeta,
    SessionCapabilities,
    SessionStatus,
    SessionTransport,
    WriterLeaseGranted,
    WriterMode,
)
from murder.runtime.sessions.persistence import SessionStore, ensure_session_schema
from murder.runtime.sessions.registry import SessionControllerRegistry


class RecordingBackend:
    async def recover(self, record: HarnessSessionRecord) -> None:
        del record

    async def send_structured_message(self, command) -> None:
        del command

    async def write_terminal_input(self, command, data: bytes) -> None:
        del command, data

    async def resize_terminal(self, command) -> None:
        del command

    async def interrupt(self, command) -> None:
        del command

    async def terminate(self, command) -> None:
        del command


class _FakeHost:
    def __init__(self, runtime: object) -> None:
        self.runtime = runtime
        self.handlers: dict[str, object] = {}

    def register_application_query(self, name: object, handler: object) -> None:
        self.handlers[str(name)] = handler

    def register_application_command(self, name: object, handler: object) -> None:
        self.handlers[str(name)] = handler


def _session_record(session_id):
    return HarnessSessionRecord(
        session_id=session_id,
        repository_id=uuid4(),
        harness="codex",
        transport=SessionTransport.TMUX,
        transport_ref="murder_test",
        status=SessionStatus.READY,
        revision=0,
        capabilities=SessionCapabilities(raw_terminal=True, structured_messages=True),
        started_at=datetime(2026, 7, 18, tzinfo=timezone.utc),
    )


@pytest.fixture
def wired_handlers():
    connection = sqlite3.connect(":memory:")
    ensure_session_schema(connection)
    store = SessionStore(connection)
    session_id = uuid4()
    store.save_session(_session_record(session_id))
    registry = SessionControllerRegistry(
        store=store,
        backend_factory=lambda _record: RecordingBackend(),
        controller_factory=SessionControllerRegistry.trusted_local_controller_factory(store),
    )
    host = _FakeHost(SimpleNamespace(db=connection, session_controllers=registry))
    sessions_handlers.register(
        host,  # type: ignore[arg-type]
        ProjectionProviderRegistry(),
        host.runtime,
    )
    return host, session_id, store, registry


@pytest.mark.asyncio
async def test_session_writer_acquire_renew_release_round_trip(wired_handlers) -> None:
    host, session_id, _store, _registry = wired_handlers
    holder = PrincipalRef(kind=PrincipalKind.CLIENT, id="tui-1")

    acquire = await host.handlers["session.writer.acquire"](
        {
            "session_id": str(session_id),
            "mode": "raw_terminal",
            "ttl_seconds": 30,
            "holder": holder.model_dump(mode="json"),
        }
    )
    assert acquire["type"] == "session.writer.granted"
    assert acquire["lease"]["holder"] == {"kind": "client", "id": "tui-1"}
    assert acquire["lease"]["mode"] == "raw_terminal"
    lease_id = acquire["lease"]["lease_id"]
    fence = acquire["lease"]["fence"]

    renew = await host.handlers["session.writer.renew"](
        {
            "session_id": str(session_id),
            "lease_id": lease_id,
            "fence": fence,
            "ttl_seconds": 30,
            "holder": holder.model_dump(mode="json"),
        }
    )
    assert renew["type"] == "session.writer.granted"
    assert renew["lease"]["lease_id"] == lease_id

    release = await host.handlers["session.writer.release"](
        {
            "session_id": str(session_id),
            "lease_id": lease_id,
            "fence": fence,
            "holder": holder.model_dump(mode="json"),
            "reason": "done",
        }
    )
    assert release["type"] == "session.writer.granted"
    assert release["lease"]["revoked_at"] is not None


@pytest.mark.asyncio
async def test_session_writer_acquire_returns_denied_shape(wired_handlers) -> None:
    host, session_id, _store, _registry = wired_handlers
    first = PrincipalRef(kind=PrincipalKind.CLIENT, id="tui-owner")
    other = PrincipalRef(kind=PrincipalKind.CLIENT, id="tui-other")

    granted = await host.handlers["session.writer.acquire"](
        {
            "session_id": str(session_id),
            "mode": "raw_terminal",
            "holder": first.model_dump(mode="json"),
        }
    )
    assert granted["type"] == "session.writer.granted"

    denied = await host.handlers["session.writer.acquire"](
        {
            "session_id": str(session_id),
            "mode": "raw_terminal",
            "holder": other.model_dump(mode="json"),
        }
    )
    assert denied["type"] == "session.writer.denied"
    assert denied["current_holder"] == {"kind": "client", "id": "tui-owner"}
    assert denied["current_mode"] == "raw_terminal"
    assert isinstance(denied["reason"], str)
    assert denied["reason"]


def test_session_writer_get_returns_active_lease(wired_handlers) -> None:
    host, session_id, store, _registry = wired_handlers
    holder = PrincipalRef(kind=PrincipalKind.CLIENT, id="tui-3")
    request_id = uuid4()
    granted = store.acquire_writer_lease(
        AcquireWriterLease(
            meta=RequestMeta(
                request_id=request_id,
                correlation=Correlation(correlation_id=request_id),
            ),
            session_id=session_id,
            mode=WriterMode.STRUCTURED,
        ),
        holder=holder,
    )
    assert isinstance(granted, WriterLeaseGranted)

    result = host.handlers["session.writer.get"]({"session_id": str(session_id)})
    assert result["ok"] is True
    assert result["lease"]["lease_id"] == str(granted.lease.lease_id)
    assert result["lease"]["mode"] == "structured"

    missing = host.handlers["session.writer.get"]({"session_id": str(uuid4())})
    assert missing == {"ok": False, "error": "not_found", "lease": None}


@pytest.mark.asyncio
async def test_session_command_execute_round_trip(wired_handlers) -> None:
    host, session_id, _store, _registry = wired_handlers
    principal = PrincipalRef(kind=PrincipalKind.SERVICE, id="trusted-local")
    operation_id = uuid4()

    result = await host.handlers["session.command.execute"](
        {
            "session_id": str(session_id),
            "command": {
                "type": "send_structured_message",
                "operation_id": str(operation_id),
                "text": "hello",
            },
            "principal": principal.model_dump(mode="json"),
        }
    )

    receipt = result["receipt"]
    assert receipt["operation_id"] == str(operation_id)
    assert receipt["session_id"] == str(session_id)
    assert receipt["revision"] == 1


@pytest.mark.asyncio
async def test_session_command_execute_requires_principal(wired_handlers) -> None:
    host, session_id, _store, _registry = wired_handlers
    operation_id = uuid4()

    with pytest.raises(ValueError, match="requires a principal"):
        await host.handlers["session.command.execute"](
            {
                "session_id": str(session_id),
                "command": {
                    "type": "send_structured_message",
                    "operation_id": str(operation_id),
                    "text": "hello",
                },
            }
        )


@pytest.mark.asyncio
async def test_session_writer_requires_live_controller_when_no_backend() -> None:
    connection = sqlite3.connect(":memory:")
    ensure_session_schema(connection)
    store = SessionStore(connection)
    session_id = uuid4()
    store.save_session(_session_record(session_id))
    registry = SessionControllerRegistry(store=store)
    host = _FakeHost(SimpleNamespace(db=connection, session_controllers=registry))
    sessions_handlers.register(
        host,  # type: ignore[arg-type]
        ProjectionProviderRegistry(),
        host.runtime,
    )
    holder = PrincipalRef(kind=PrincipalKind.SERVICE, id="trusted-local")

    with pytest.raises(RuntimeError, match="no live controller"):
        await host.handlers["session.writer.acquire"](
            {
                "session_id": str(session_id),
                "mode": "structured",
                "holder": holder.model_dump(mode="json"),
            }
        )
