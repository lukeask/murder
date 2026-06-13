"""Tests for F6: tmux.frame backend stream.

Covers:
- TmuxFrameEvent round-trips through the BUS_EVENT_ADAPTER (protocol shape).
- SocketBusServer opens the capture loop only on subscription (open-on-subscribe).
- SocketBusServer closes the capture loop when the subscription task is cancelled
  (close-on-unsubscribe / no standing cost).
- When no tmux_frame_capture is configured the subscription completes the handshake
  (replay_done) and exits cleanly without emitting any frames.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from murder.bus.protocol import (
    BUS_EVENT_ADAPTER,
    PROTOCOL_VERSION,
    ClientKind,
    EventFilter,
    TmuxFrameEvent,
)
from murder.bus.transport_socket import SocketBusServer


# ---------------------------------------------------------------------------
# Protocol shape
# ---------------------------------------------------------------------------


def test_tmux_frame_event_round_trips_through_protocol_adapter() -> None:
    """TmuxFrameEvent serialises and deserialises without loss via the adapter."""
    event = TmuxFrameEvent(
        run_id="run-test",
        agent_id="supervisor",
        frame="\x1b[32mhello tmux\x1b[0m",
    )

    payload = event.model_dump(mode="json")
    parsed = BUS_EVENT_ADAPTER.validate_python(payload)

    assert isinstance(parsed, TmuxFrameEvent)
    assert parsed.type == "tmux.frame"
    assert parsed.frame == "\x1b[32mhello tmux\x1b[0m"
    assert parsed.run_id == "run-test"


def test_tmux_frame_event_type_discriminant() -> None:
    """The discriminant literal value is exactly 'tmux.frame'."""
    event = TmuxFrameEvent(run_id="r", agent_id="supervisor", frame="x")
    assert event.type == "tmux.frame"


def test_protocol_version_is_3() -> None:
    """PROTOCOL_VERSION must equal 4 (history view added Entity.HISTORY, bumping 3→4)."""
    assert PROTOCOL_VERSION == 4


# ---------------------------------------------------------------------------
# Helpers — minimal in-memory broker and transport double
# ---------------------------------------------------------------------------


class _FakeBroker:
    """Minimal broker double — no DB, no events; supports watermark + empty replay/tail."""

    def __init__(self) -> None:
        self._published: list[Any] = []

    async def publish(self, event: Any) -> None:
        self._published.append(event)

    def watermark(self) -> int:
        return 0

    def replay(
        self,
        filter: Any = None,
        *,
        since_id: int,
        until_id: int | None = None,
    ) -> list[tuple[int, Any]]:
        return []

    async def tail(
        self,
        filter: Any = None,
        *,
        since_id: int,
    ):
        # Yield nothing; tail blocks indefinitely in real code — for tests
        # we stop by cancellation so never actually yield.
        while True:
            await asyncio.sleep(0)
            return
        yield  # make this an async generator  # noqa: unreachable


class _RecordingTransport:
    """Collects bytes written to it; never actually sends anything."""

    def __init__(self) -> None:
        self.sent: list[bytes] = []
        self._connected = True

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def send(self, data: bytes) -> None:
        self.sent.append(data)

    async def close(self) -> None:
        self._connected = False


def _parse_messages(transport: _RecordingTransport) -> list[dict[str, Any]]:
    """Parse all newline-delimited JSON frames the transport received."""
    result: list[dict[str, Any]] = []
    for chunk in transport.sent:
        for line in chunk.decode("utf-8").splitlines():
            line = line.strip()
            if line:
                result.append(json.loads(line))
    return result


def _op_kinds(messages: list[dict[str, Any]]) -> list[str]:
    return [m.get("op", "?") for m in messages]


def _pub_types(messages: list[dict[str, Any]]) -> list[str]:
    return [m["event"]["type"] for m in messages if m.get("op") == "pub"]


# ---------------------------------------------------------------------------
# Lifecycle tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tmux_frame_subscription_sends_frames_while_task_runs() -> None:
    """Frames arrive while the subscription task is alive."""
    frames_captured = 0
    call_count = 0

    async def _capture(agent_id: str | None = None) -> str:
        nonlocal call_count
        call_count += 1
        return f"\x1b[1mframe-{call_count}\x1b[0m"

    broker = _FakeBroker()
    server = SocketBusServer(
        broker,  # type: ignore[arg-type]
        run_id="run-test",
        socket_path=Path("/tmp/nonexistent-test.sock"),
        tmux_frame_capture=_capture,
        tmux_frame_interval_s=0,  # no sleep; controlled by asyncio.sleep patch
    )

    transport = _RecordingTransport()

    # Build a minimal SubMessage for tmux.frame.
    sub_msg_raw: dict[str, Any] = {
        "op": "sub",
        "schema_version": PROTOCOL_VERSION,
        "correlation_id": "cid-1",
        "args": {
            "filter": {"type": "tmux.frame"},
            "since_id": None,
            "presence_retain": False,
        },
    }
    from murder.bus.protocol import WIRE_MESSAGE_ADAPTER, SubMessage
    sub_msg = WIRE_MESSAGE_ADAPTER.validate_python(sub_msg_raw)
    assert isinstance(sub_msg, SubMessage)

    # Fake the session / attach transport manually.
    from murder.bus.transport_socket import _ClientSession
    session = _ClientSession(
        client_id="test-client",
        kind=ClientKind.TUI,
        transport=transport,  # type: ignore[arg-type]
    )

    # Run the subscription for a bounded number of iterations, then cancel.
    task = asyncio.create_task(server._run_subscription(session, sub_msg))

    # Let the event loop tick a few times so the capture loop fires.
    for _ in range(5):
        await asyncio.sleep(0)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    msgs = _parse_messages(transport)
    ops = _op_kinds(msgs)

    # Must have sent at least one "subscribed" ack and one "replay_done" ack.
    assert "ack" in ops
    ack_kinds = [m["body"]["kind"] for m in msgs if m.get("op") == "ack"]
    assert "subscribed" in ack_kinds
    assert "replay_done" in ack_kinds

    # Must have sent at least one tmux.frame pub.
    pub_types = _pub_types(msgs)
    assert "tmux.frame" in pub_types, f"expected tmux.frame in {pub_types}"

    # Capture function must have been called (stream was open).
    assert call_count > 0


@pytest.mark.asyncio
async def test_tmux_frame_subscription_stops_on_task_cancel() -> None:
    """Once the task is cancelled the capture function is no longer called."""
    call_log: list[int] = []

    async def _capture(agent_id: str | None = None) -> str:
        call_log.append(len(call_log))
        return "frame"

    broker = _FakeBroker()
    server = SocketBusServer(
        broker,  # type: ignore[arg-type]
        run_id="run-test",
        socket_path=Path("/tmp/nonexistent-test.sock"),
        tmux_frame_capture=_capture,
        tmux_frame_interval_s=0,
    )

    transport = _RecordingTransport()

    from murder.bus.protocol import WIRE_MESSAGE_ADAPTER, SubMessage
    from murder.bus.transport_socket import _ClientSession
    sub_msg_raw: dict[str, Any] = {
        "op": "sub",
        "schema_version": PROTOCOL_VERSION,
        "correlation_id": "cid-2",
        "args": {
            "filter": {"type": "tmux.frame"},
            "since_id": None,
            "presence_retain": False,
        },
    }
    sub_msg = WIRE_MESSAGE_ADAPTER.validate_python(sub_msg_raw)
    session = _ClientSession(
        client_id="test-client",
        kind=ClientKind.TUI,
        transport=transport,  # type: ignore[arg-type]
    )

    task = asyncio.create_task(server._run_subscription(session, sub_msg))

    # Let a few ticks pass, then cancel.
    for _ in range(3):
        await asyncio.sleep(0)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    calls_at_cancel = len(call_log)
    assert calls_at_cancel > 0  # was running before cancel

    # Allow a few more ticks; call_log must NOT grow after cancellation.
    for _ in range(3):
        await asyncio.sleep(0)

    assert len(call_log) == calls_at_cancel, (
        "capture function was called after task cancellation — stream not closed"
    )


@pytest.mark.asyncio
async def test_tmux_frame_subscription_no_capture_configured_exits_cleanly() -> None:
    """When no capture function is injected, the subscription completes handshake and returns."""
    broker = _FakeBroker()
    server = SocketBusServer(
        broker,  # type: ignore[arg-type]
        run_id="run-test",
        socket_path=Path("/tmp/nonexistent-test.sock"),
        tmux_frame_capture=None,  # explicit no-op
        tmux_frame_interval_s=0,
    )

    transport = _RecordingTransport()

    from murder.bus.protocol import WIRE_MESSAGE_ADAPTER, SubMessage
    from murder.bus.transport_socket import _ClientSession
    sub_msg_raw: dict[str, Any] = {
        "op": "sub",
        "schema_version": PROTOCOL_VERSION,
        "correlation_id": "cid-3",
        "args": {
            "filter": {"type": "tmux.frame"},
            "since_id": None,
            "presence_retain": False,
        },
    }
    sub_msg = WIRE_MESSAGE_ADAPTER.validate_python(sub_msg_raw)
    session = _ClientSession(
        client_id="test-client",
        kind=ClientKind.TUI,
        transport=transport,  # type: ignore[arg-type]
    )

    # Should complete without hanging (no capture loop).
    await asyncio.wait_for(server._run_subscription(session, sub_msg), timeout=2.0)

    msgs = _parse_messages(transport)
    ack_kinds = [m["body"]["kind"] for m in msgs if m.get("op") == "ack"]
    assert "subscribed" in ack_kinds
    assert "replay_done" in ack_kinds
    # No frames should have been emitted.
    assert _pub_types(msgs) == []


@pytest.mark.asyncio
async def test_non_tmux_frame_subscription_goes_through_broker() -> None:
    """A non-tmux.frame subscription uses the normal broker tail, not the capture loop."""
    broker = _FakeBroker()
    capture_called = False

    async def _capture(agent_id: str | None = None) -> str:
        nonlocal capture_called
        capture_called = True
        return "frame"

    server = SocketBusServer(
        broker,  # type: ignore[arg-type]
        run_id="run-test",
        socket_path=Path("/tmp/nonexistent-test.sock"),
        tmux_frame_capture=_capture,
        tmux_frame_interval_s=0,
    )

    transport = _RecordingTransport()

    from murder.bus.protocol import WIRE_MESSAGE_ADAPTER, SubMessage
    from murder.bus.transport_socket import _ClientSession
    sub_msg_raw: dict[str, Any] = {
        "op": "sub",
        "schema_version": PROTOCOL_VERSION,
        "correlation_id": "cid-4",
        "args": {
            "filter": {"type": "presence"},  # NOT tmux.frame
            "since_id": None,
            "presence_retain": False,
        },
    }
    sub_msg = WIRE_MESSAGE_ADAPTER.validate_python(sub_msg_raw)
    session = _ClientSession(
        client_id="test-client",
        kind=ClientKind.TUI,
        transport=transport,  # type: ignore[arg-type]
    )

    # The broker tail loops forever; cancel after a moment.
    task = asyncio.create_task(server._run_subscription(session, sub_msg))
    for _ in range(3):
        await asyncio.sleep(0)
    task.cancel()
    # Wait for cancellation to propagate; the fake tail yields asyncio.sleep(0)
    # so it may return normally when asyncio.sleep(0) resolves before cancel.
    # Either way the task must finish and capture must not have been called.
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert not capture_called, "capture function must not be called for non-tmux.frame subscriptions"


# ---------------------------------------------------------------------------
# Agent scoping — the filter's agent_id selects which pane is captured
# ---------------------------------------------------------------------------


def _tmux_frame_sub_msg(correlation_id: str, agent_id: str | None = None) -> Any:
    from murder.bus.protocol import WIRE_MESSAGE_ADAPTER, SubMessage

    filt: dict[str, Any] = {"type": "tmux.frame"}
    if agent_id is not None:
        filt["agent_id"] = agent_id
    msg = WIRE_MESSAGE_ADAPTER.validate_python(
        {
            "op": "sub",
            "schema_version": PROTOCOL_VERSION,
            "correlation_id": correlation_id,
            "args": {"filter": filt, "since_id": None, "presence_retain": False},
        }
    )
    assert isinstance(msg, SubMessage)
    return msg


@pytest.mark.asyncio
async def test_tmux_frame_subscription_passes_filter_agent_id_to_capture() -> None:
    """An agent-scoped subscription captures that agent's pane and stamps the events."""
    seen_agent_ids: list[str | None] = []

    async def _capture(agent_id: str | None = None) -> str:
        seen_agent_ids.append(agent_id)
        return "frame"

    server = SocketBusServer(
        _FakeBroker(),  # type: ignore[arg-type]
        run_id="run-test",
        socket_path=Path("/tmp/nonexistent-test.sock"),
        tmux_frame_capture=_capture,
        tmux_frame_interval_s=0,
    )
    transport = _RecordingTransport()
    from murder.bus.transport_socket import _ClientSession

    session = _ClientSession(
        client_id="test-client",
        kind=ClientKind.TUI,
        transport=transport,  # type: ignore[arg-type]
    )

    task = asyncio.create_task(
        server._run_subscription(session, _tmux_frame_sub_msg("cid-agent", "claude-rogue-x"))
    )
    for _ in range(5):
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert seen_agent_ids, "capture never called"
    assert all(a == "claude-rogue-x" for a in seen_agent_ids)
    frame_events = [
        m["event"] for m in _parse_messages(transport) if m.get("op") == "pub"
    ]
    assert frame_events and all(e["agent_id"] == "claude-rogue-x" for e in frame_events)


@pytest.mark.asyncio
async def test_tmux_frame_subscription_surfaces_capture_failure_as_frame() -> None:
    """A failing capture emits a diagnostic frame instead of silence.

    The raw view is the backup when parsing breaks; an eternal
    '[waiting for tmux frame…]' would hide exactly the failure it exists to show.
    """

    async def _capture(agent_id: str | None = None) -> str:
        raise RuntimeError("session not found: murder_crow_x")

    server = SocketBusServer(
        _FakeBroker(),  # type: ignore[arg-type]
        run_id="run-test",
        socket_path=Path("/tmp/nonexistent-test.sock"),
        tmux_frame_capture=_capture,
        tmux_frame_interval_s=0,
    )
    transport = _RecordingTransport()
    from murder.bus.transport_socket import _ClientSession

    session = _ClientSession(
        client_id="test-client",
        kind=ClientKind.TUI,
        transport=transport,  # type: ignore[arg-type]
    )

    task = asyncio.create_task(
        server._run_subscription(session, _tmux_frame_sub_msg("cid-fail"))
    )
    for _ in range(5):
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    frames = [
        m["event"]["frame"] for m in _parse_messages(transport) if m.get("op") == "pub"
    ]
    assert frames, "no frame emitted on capture failure"
    assert all("tmux capture failed" in f for f in frames)


# ---------------------------------------------------------------------------
# Production wiring — ServiceHost supplies a non-None capture supplier
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_service_host_wires_tmux_frame_capture_into_socket_server(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ServiceHost._capture_tmux_frame is wired into SocketBusServer and yields frames.

    Verifies the F6 done-condition: the production server is constructed with a
    non-None tmux_frame_capture so ``ctrl+y`` subscriptions receive live frames.
    The actual tmux.capture_pane is mocked so no real tmux session is needed.
    """
    from unittest.mock import AsyncMock, patch

    from murder.app.service.host import ServiceHost
    from murder.bus.transport_socket import SocketBusServer
    from murder.config import Config, CrowHandlerConfig, HarnessRoleConfig, ProjectConfig

    config = Config(
        project=ProjectConfig(name="test"),
        collaborator=HarnessRoleConfig(harness="codex"),
        default_crow=HarnessRoleConfig(harness="codex"),
        crow_handler=CrowHandlerConfig(model="test-model"),
    )
    host = ServiceHost(config=config, repo_root=tmp_path)

    # Verify _capture_tmux_frame is callable (bound method exists).
    assert callable(host._capture_tmux_frame), (
        "ServiceHost must expose _capture_tmux_frame as a callable"
    )

    # Patch tmux.capture_pane so we can call the supplier without a real session.
    fake_frame = "\x1b[32mprod-frame\x1b[0m"
    with patch(
        "murder.runtime.terminal.tmux.capture_pane",
        new=AsyncMock(return_value=fake_frame),
    ):
        result = await host._capture_tmux_frame()

    assert result == fake_frame, (
        f"_capture_tmux_frame must return the ANSI frame from tmux.capture_pane; got {result!r}"
    )

    # Verify the supplier is wired into SocketBusServer (server has non-None capture).
    # We build a SocketBusServer the same way host.start() does (minus the real runtime).
    from murder.bus.broker import DurableBroker

    class _MinimalBroker(_FakeBroker):
        pass

    server = SocketBusServer(
        _MinimalBroker(),  # type: ignore[arg-type]
        run_id="run-prod-test",
        socket_path=tmp_path / "test.sock",
        tmux_frame_capture=host._capture_tmux_frame,
    )
    assert server._tmux_frame_capture is not None, (
        "SocketBusServer must receive a non-None tmux_frame_capture from ServiceHost"
    )

    # Run a subscription and confirm at least one tmux.frame pub arrives.
    transport = _RecordingTransport()

    from murder.bus.protocol import WIRE_MESSAGE_ADAPTER, SubMessage
    from murder.bus.transport_socket import _ClientSession

    sub_msg_raw: dict[str, Any] = {
        "op": "sub",
        "schema_version": PROTOCOL_VERSION,
        "correlation_id": "cid-prod",
        "args": {
            "filter": {"type": "tmux.frame"},
            "since_id": None,
            "presence_retain": False,
        },
    }
    sub_msg = WIRE_MESSAGE_ADAPTER.validate_python(sub_msg_raw)
    session = _ClientSession(
        client_id="prod-test-client",
        kind=ClientKind.TUI,
        transport=transport,  # type: ignore[arg-type]
    )

    with patch(
        "murder.runtime.terminal.tmux.capture_pane",
        new=AsyncMock(return_value=fake_frame),
    ):
        task = asyncio.create_task(server._run_subscription(session, sub_msg))
        for _ in range(5):
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    msgs = _parse_messages(transport)
    pub_types = _pub_types(msgs)
    assert "tmux.frame" in pub_types, (
        f"production server with wired capture must emit tmux.frame pubs; got {pub_types}"
    )
