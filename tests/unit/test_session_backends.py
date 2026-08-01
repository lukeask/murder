from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from murder.runtime.sessions.backend import AppServerSessionBackend, TmuxSessionBackend
from murder.runtime.sessions.contracts import (
    AcquireWriterLease,
    Correlation,
    HarnessSessionRecord,
    InterruptSession,
    PrincipalKind,
    PrincipalRef,
    RequestMeta,
    ResizeTerminal,
    SendStructuredMessage,
    SessionCapabilities,
    SessionStatus,
    SessionTransport,
    WriterLeaseGranted,
    WriterMode,
    WriteTerminalInput,
)
from murder.runtime.sessions.controller import (
    SessionCapabilityError,
    SessionController,
    trusted_local_session_authorizer,
)
from murder.runtime.sessions.persistence import SessionStore, ensure_session_schema
from tests.support.database import open_test_repo_db


class RecordingAppServerClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    async def send_message(
        self,
        *,
        operation_id: str,
        text: str,
        activity_id: str | None,
    ) -> None:
        self.calls.append(("message", (operation_id, text, activity_id)))

    async def write_terminal(self, data: bytes) -> None:
        self.calls.append(("terminal", data))

    async def resize_terminal(self, *, columns: int, rows: int) -> None:
        self.calls.append(("resize", (columns, rows)))

    async def interrupt(self, *, reason: str | None) -> None:
        self.calls.append(("interrupt", reason))

    async def terminate(self, *, force: bool, reason: str | None) -> None:
        self.calls.append(("terminate", (force, reason)))

    async def recover(self, record: HarnessSessionRecord) -> None:
        self.calls.append(("recover", record.session_id))


def test_app_server_and_terminal_capabilities_share_controller_boundary(tmp_path) -> None:
    async def scenario() -> None:
        db = open_test_repo_db(tmp_path / "murder.db")
        ensure_session_schema(db.conn)
        client = RecordingAppServerClient()
        record = HarnessSessionRecord(
            session_id=uuid4(),
            repository_id=db.repository_id,
            harness="structured-test",
            transport=SessionTransport.APP_SERVER,
            transport_ref="app-server:thread-1",
            status=SessionStatus.READY,
            revision=0,
            capabilities=SessionCapabilities(
                structured_messages=True,
                raw_terminal=False,
            ),
            started_at=datetime(2026, 7, 18, tzinfo=timezone.utc),
        )
        controller = SessionController(
            record=record,
            store=SessionStore(db),
            backend=AppServerSessionBackend(client),
            authorizer=trusted_local_session_authorizer,
        )
        operation_id = uuid4()
        await controller.execute(
            SendStructuredMessage(operation_id=operation_id, text="hello"),
            principal=PrincipalRef(kind=PrincipalKind.SERVICE, id="test-service"),
        )
        with pytest.raises(SessionCapabilityError):
            await controller.execute(
                WriteTerminalInput(
                    operation_id=uuid4(),
                    lease_id=uuid4(),
                    fence=1,
                    data="not supported",
                ),
                principal=PrincipalRef(kind=PrincipalKind.CLIENT, id="terminal"),
            )
        assert client.calls == [
            ("message", (str(operation_id), "hello", None)),
        ]
        await controller.close()

    asyncio.run(scenario())


def test_terminal_only_tmux_backend_uses_same_fenced_controller(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, object]] = []

    async def session_exists(session: str) -> bool:
        calls.append(("exists", session))
        return True

    async def send_bytes(session: str, data: bytes) -> None:
        calls.append(("bytes", (session, data)))

    async def send_keys(
        session: str,
        text: str,
        *,
        literal: bool,
        enter: bool,
    ) -> None:
        calls.append(("keys", (session, text, literal, enter)))

    async def resize_session(session: str, *, columns: int, rows: int) -> None:
        calls.append(("resize", (session, columns, rows)))

    async def kill_session(session: str) -> None:
        calls.append(("kill", session))

    monkeypatch.setattr("murder.runtime.sessions.backend.tmux.session_exists", session_exists)
    monkeypatch.setattr("murder.runtime.sessions.backend.tmux.send_bytes", send_bytes)
    monkeypatch.setattr("murder.runtime.sessions.backend.tmux.send_keys", send_keys)
    monkeypatch.setattr("murder.runtime.sessions.backend.tmux.resize_session", resize_session)
    monkeypatch.setattr("murder.runtime.sessions.backend.tmux.kill_session", kill_session)

    async def scenario() -> None:
        db = open_test_repo_db(tmp_path / "murder.db")
        ensure_session_schema(db.conn)
        record = HarnessSessionRecord(
            session_id=uuid4(),
            repository_id=db.repository_id,
            harness="terminal-only",
            transport=SessionTransport.TMUX,
            transport_ref="tmux-terminal-only",
            status=SessionStatus.READY,
            revision=0,
            capabilities=SessionCapabilities(raw_terminal=True, interruptible=True),
            started_at=datetime(2026, 7, 18, tzinfo=timezone.utc),
        )
        controller = SessionController(
            record=record,
            store=SessionStore(db),
            backend=TmuxSessionBackend(record.transport_ref),
            authorizer=trusted_local_session_authorizer,
        )
        await controller.recover()
        principal = PrincipalRef(kind=PrincipalKind.CLIENT, id="terminal-client")
        request_id = uuid4()
        lease = await controller.acquire_writer_lease(
            AcquireWriterLease(
                meta=RequestMeta(
                    request_id=request_id,
                    correlation=Correlation(correlation_id=request_id),
                ),
                session_id=record.session_id,
                mode=WriterMode.RAW_TERMINAL,
            ),
            holder=principal,
        )
        assert isinstance(lease, WriterLeaseGranted)
        await controller.execute(
            WriteTerminalInput(
                operation_id=uuid4(),
                lease_id=lease.lease.lease_id,
                fence=lease.lease.fence,
                data="hello",
            ),
            principal=principal,
        )
        await controller.execute(
            ResizeTerminal(operation_id=uuid4(), columns=100, rows=30),
            principal=PrincipalRef(kind=PrincipalKind.SERVICE, id="service"),
        )
        await controller.execute(
            InterruptSession(operation_id=uuid4()),
            principal=PrincipalRef(kind=PrincipalKind.SERVICE, id="service"),
        )
        await controller.close()

    asyncio.run(scenario())
    assert calls == [
        ("exists", "tmux-terminal-only"),
        ("bytes", ("tmux-terminal-only", b"hello")),
        ("resize", ("tmux-terminal-only", 100, 30)),
        ("keys", ("tmux-terminal-only", "C-c", False, False)),
    ]
