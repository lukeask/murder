from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest
from pydantic import ValidationError

from murder.app.protocol.terminal import TerminalFrame, TerminalTarget
from murder.app.service.host import ServiceHost
from murder.app.service.read_model import ServiceReadModel
from murder.bus.transport_socket import (
    CapturedTerminalFrame,
    SocketBusServer,
)
from murder.runtime.sessions.contracts import (
    HarnessSessionRecord,
    SessionCapabilities,
    SessionStatus,
    SessionTransport,
)
from murder.runtime.sessions.persistence import (
    SessionStore,
    ensure_session_schema,
)
from murder.runtime.terminal import tmux
from murder.state.persistence.agents import upsert_agent
from murder.state.persistence.schema import get_db, init_db


class _Broker:
    def watermark(self) -> int:
        return 0


class _TerminalRecordingTransport:
    def __init__(self) -> None:
        self.control: list[bytes] = []
        self.terminal: list[tuple[str, bytes]] = []
        self.sent = asyncio.Event()

    async def send(self, payload: bytes) -> None:
        self.control.append(payload)

    async def send_terminal(self, payload: bytes, *, stream_id: str) -> None:
        self.terminal.append((stream_id, payload))
        self.sent.set()


def test_terminal_target_uses_uuid_and_names_legacy_bridge_explicitly() -> None:
    session_id = uuid4()
    assert TerminalTarget(session_id=session_id).session_id == session_id
    assert TerminalTarget(legacy_agent_id="crow-7").legacy_agent_id == "crow-7"
    assert TerminalTarget().session_id is None  # supervisor compatibility

    with pytest.raises(ValidationError):
        TerminalTarget.model_validate({"session_id": "crow-7"})
    with pytest.raises(ValidationError):
        TerminalTarget(session_id=session_id, legacy_agent_id="crow-7")


def test_terminal_output_contract_does_not_advertise_undecoded_base64() -> None:
    with pytest.raises(ValidationError):
        TerminalFrame.model_validate(
            {
                "subscription_id": "term-1",
                "session_id": str(uuid4()),
                "sequence": 1,
                "captured_at": datetime.now(timezone.utc),
                "columns": 80,
                "rows": 24,
                "encoding": "base64",
                "data": "SGVsbG8=",
            }
        )


@pytest.mark.asyncio
async def test_captured_tmux_geometry_is_adapter_geometry_not_text_shape() -> None:
    session_id = uuid4()
    observed: list[str | None] = []

    async def capture(target_id: str | None) -> CapturedTerminalFrame:
        observed.append(target_id)
        return CapturedTerminalFrame(
            data="short\ntext",
            columns=173,
            rows=61,
        )

    server = SocketBusServer(
        _Broker(),  # type: ignore[arg-type]
        run_id="run-1",
        tmux_frame_capture=capture,
    )
    frame = await server._capture_terminal_replacement(
        TerminalTarget(session_id=session_id),
        after_sequence=0,
        subscription_id="term-1",
    )

    assert observed == [str(session_id)]
    assert (frame.columns, frame.rows) == (173, 61)
    assert frame.session_id == session_id
    assert frame.legacy_agent_id is None


@pytest.mark.asyncio
async def test_service_host_resolves_persisted_uuid_to_tmux_transport_ref(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = sqlite3.connect(":memory:", isolation_level=None)
    connection.row_factory = sqlite3.Row
    ensure_session_schema(connection)
    session_id = uuid4()
    SessionStore(connection).save_session(
        HarnessSessionRecord(
            session_id=session_id,
            repository_id=uuid4(),
            harness="codex",
            transport=SessionTransport.TMUX,
            transport_ref="murder_exact_tmux_ref",
            status=SessionStatus.READY,
            revision=0,
            capabilities=SessionCapabilities(),
            started_at=datetime.now(timezone.utc),
        )
    )
    seen: list[tuple[str, str]] = []

    async def capture_viewport(name: str, *, escapes: bool = False) -> str:
        seen.append(("capture", name))
        assert escapes is True
        return "pane"

    async def dimensions(name: str) -> tuple[int, int]:
        seen.append(("geometry", name))
        return 144, 52

    monkeypatch.setattr(tmux, "capture_viewport", capture_viewport)
    monkeypatch.setattr(tmux, "pane_dimensions", dimensions)
    host = object.__new__(ServiceHost)
    host.repo_root = tmp_path
    host.runtime = cast(Any, type("_Runtime", (), {"db": connection})())

    captured = await host._capture_tmux_frame(str(session_id))
    assert captured == CapturedTerminalFrame(data="pane", columns=144, rows=52)
    assert seen == [
        ("capture", "murder_exact_tmux_ref"),
        ("geometry", "murder_exact_tmux_ref"),
    ]


def test_roster_projects_persisted_session_uuid_for_client_attachment(
    repo_root: Path,
) -> None:
    database_path = repo_root / ".murder" / "murder.db"
    connection = get_db(database_path)
    init_db(connection)
    tmux_ref = "murder_repo_crow_t123"
    upsert_agent(
        connection,
        agent_id="crow-t123",
        role="crow",
        ticket_id=None,
        session=tmux_ref,
        status="running",
    )
    session_id = uuid4()
    SessionStore(connection).save_session(
        HarnessSessionRecord(
            session_id=session_id,
            repository_id=uuid4(),
            harness="codex",
            transport=SessionTransport.TMUX,
            transport_ref=tmux_ref,
            status=SessionStatus.READY,
            revision=0,
            capabilities=SessionCapabilities(),
            started_at=datetime.now(timezone.utc),
        )
    )

    snapshot = ServiceReadModel(database_path).get_crow_snapshot()
    assert snapshot.sessions[0].session_id == str(session_id)


@pytest.mark.asyncio
async def test_legacy_tmux_frames_use_bounded_terminal_lane_not_control_lane() -> None:
    async def capture(target_id: str | None) -> CapturedTerminalFrame:
        return CapturedTerminalFrame(
            data=f"frame for {target_id}",
            columns=120,
            rows=40,
        )

    server = SocketBusServer(
        _Broker(),  # type: ignore[arg-type]
        run_id="run-1",
        tmux_frame_capture=capture,
        tmux_frame_interval_s=60,
    )
    transport = _TerminalRecordingTransport()
    task = asyncio.create_task(
        server._run_tmux_frame_stream(
            transport,  # type: ignore[arg-type]
            "legacy-subscription",
            agent_id="crow-7",
        )
    )
    await asyncio.wait_for(transport.sent.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert transport.control == []
    assert len(transport.terminal) == 1
    stream_id, payload = transport.terminal[0]
    assert stream_id == "legacy-tmux:legacy-subscription"
    message = json.loads(payload)
    assert message["op"] == "pub"
    assert message["event"]["type"] == "tmux.frame"
    assert message["event"]["frame"] == "frame for crow-7"
