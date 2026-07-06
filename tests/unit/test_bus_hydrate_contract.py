"""Hydrate operation contract tests.

These tests pin the hydrationduration plan's replacement for cold-start
``primeSlices`` plus subscription replay. They are intentionally written
against the intended wire/server contract so they fail clearly while the
production hydrate op is still being implemented.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from murder.bus.protocol import (
    PROTOCOL_VERSION,
    AckMessage,
    ClientKind,
    Entity,
    ErrorEvent,
    EventFilter,
    PubMessage,
    StateSnapshotEvent,
    WIRE_MESSAGE_ADAPTER,
)
from murder.bus.transport_socket import SocketBusServer, _ClientSession

HYDRATE_ALL_REQUESTS = [
    "state.conversations_snapshot",
    "state.crow_snapshot",
    "state.schedule_snapshot",
    "tui.load_favorites",
    "tui.load_templates",
    "tui.load_themes",
    "tui.load_workflows",
    "settings.get",
]


class _HydrateBroker:
    """Broker double for hydrate cookbook cases."""

    def __init__(self, *, watermark: int = 10, retained_floor: int = 0) -> None:
        self._watermark = watermark
        self.retained_floor = retained_floor
        self.replay_calls: list[tuple[int, int | None, str | None]] = []
        self.tail_calls: list[tuple[int, str | None]] = []
        self.request_calls: list[str] = []
        self.replay_events: list[tuple[int, Any]] = []
        self.tail_events: asyncio.Queue[tuple[int, Any]] = asyncio.Queue()

    def watermark(self) -> int:
        return self._watermark

    def cursor_retained(self, cursor: int) -> bool:
        return cursor >= self.retained_floor

    async def request(
        self,
        target: str,
        body: dict[str, Any],
        *,
        timeout_s: float,
    ) -> dict[str, Any]:
        self.request_calls.append(target)
        return {"target": target, "body": body, "timeout_s": timeout_s}

    def replay(
        self,
        filter: EventFilter | None = None,
        *,
        since_id: int,
        until_id: int | None = None,
    ) -> list[tuple[int, Any]]:
        self.replay_calls.append((since_id, until_id, filter.type if filter else None))
        return [
            (seq, event)
            for seq, event in self.replay_events
            if seq > since_id
            and (until_id is None or seq <= until_id)
            and (filter is None or filter.matches(event))
        ]

    async def tail(self, filter: EventFilter | None = None, *, since_id: int):
        self.tail_calls.append((since_id, filter.type if filter else None))
        while True:
            yield await self.tail_events.get()


class _RecordingTransport:
    def __init__(self) -> None:
        self.sent: list[bytes] = []

    async def send(self, data: bytes) -> None:
        self.sent.append(data)


def _event(message: str) -> ErrorEvent:
    return ErrorEvent(run_id="run-test", message=message)


def _state_snapshot(key: str) -> StateSnapshotEvent:
    return StateSnapshotEvent(run_id="run-test", entity=Entity.AGENT, key=key)


def _hydrate_message(*, cursor: int | None = None, topics: list[str] | None = None) -> Any:
    raw: dict[str, Any] = {
        "op": "hydrate",
        "schema_version": PROTOCOL_VERSION,
        "correlation_id": "hydrate-test",
        "args": {
            "topics": topics or ["all"],
            "cursor": cursor,
        },
    }
    msg = WIRE_MESSAGE_ADAPTER.validate_python(raw)
    assert getattr(msg, "op") == "hydrate"
    return msg


def _server(broker: _HydrateBroker) -> SocketBusServer:
    return SocketBusServer(
        broker,  # type: ignore[arg-type]
        run_id="run-test",
        socket_path=Path("/tmp/nonexistent-hydrate-test.sock"),
    )


def _session(transport: _RecordingTransport) -> _ClientSession:
    return _ClientSession(
        client_id="tui-test",
        kind=ClientKind.TUI,
        transport=transport,  # type: ignore[arg-type]
    )


def _decode_sent(transport: _RecordingTransport) -> list[Any]:
    return [
        WIRE_MESSAGE_ADAPTER.validate_json(frame.decode("utf-8"))
        for chunk in transport.sent
        for frame in chunk.splitlines(keepends=True)
    ]


async def _run_hydrate_once(
    server: SocketBusServer,
    session: _ClientSession,
    msg: Any,
) -> None:
    handler = getattr(server, "_handle_hydrate", None)
    assert callable(handler), "SocketBusServer must implement _handle_hydrate for op='hydrate'"
    await handler(session, msg)
    await asyncio.sleep(0)
    for task in list(session.subscriptions):
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


def _hydrate_ack(transport: _RecordingTransport) -> AckMessage:
    acks = [msg for msg in _decode_sent(transport) if isinstance(msg, AckMessage)]
    assert len(acks) == 1
    return acks[0]


def test_hydrate_wire_contract_is_declared() -> None:
    msg = _hydrate_message(cursor=123, topics=["conversations", "schedule"])

    assert msg.correlation_id == "hydrate-test"
    assert msg.args.topics == ["conversations", "schedule"]
    assert msg.args.cursor == 123


@pytest.mark.asyncio
async def test_cold_hydrate_delivers_snapshot_and_no_historical_frames() -> None:
    broker = _HydrateBroker(watermark=99)
    broker.replay_events.append((1, _event("historical")))
    transport = _RecordingTransport()

    await _run_hydrate_once(_server(broker), _session(transport), _hydrate_message())

    sent = _decode_sent(transport)
    assert not [msg for msg in sent if isinstance(msg, PubMessage)]
    assert broker.replay_calls == []
    assert broker.request_calls == HYDRATE_ALL_REQUESTS
    assert broker.tail_calls == [
        (99, "state.snapshot"),
        (99, "conversation.block"),
        (99, "conversation.state"),
        (99, "error"),
    ]
    ack = _hydrate_ack(transport)
    assert ack.body.kind == "hydrate_reply"
    assert ack.body.watermark == 99
    assert ack.body.result is not None
    assert ack.body.result["mode"] == "cold"
    assert ack.body.result["cursor"] == 99


@pytest.mark.asyncio
async def test_event_published_mid_snapshot_build_is_tailed_after_snapshot() -> None:
    broker = _HydrateBroker(watermark=5)
    transport = _RecordingTransport()

    await _run_hydrate_once(_server(broker), _session(transport), _hydrate_message())

    assert broker.request_calls == HYDRATE_ALL_REQUESTS
    assert broker.tail_calls == [
        (5, "state.snapshot"),
        (5, "conversation.block"),
        (5, "conversation.state"),
        (5, "error"),
    ]


@pytest.mark.asyncio
async def test_resume_with_live_cursor_replays_only_delta_and_skips_snapshots() -> None:
    broker = _HydrateBroker(watermark=20, retained_floor=10)
    broker.replay_events.extend([(9, _state_snapshot("old")), (15, _state_snapshot("delta"))])
    transport = _RecordingTransport()

    await _run_hydrate_once(_server(broker), _session(transport), _hydrate_message(cursor=10))

    assert broker.request_calls == []
    assert broker.replay_calls == [
        (10, 20, "state.snapshot"),
        (10, 20, "conversation.block"),
        (10, 20, "conversation.state"),
    ]
    assert broker.tail_calls == [
        (20, "state.snapshot"),
        (20, "conversation.block"),
        (20, "conversation.state"),
        (20, "error"),
    ]
    ack = _hydrate_ack(transport)
    assert ack.body.result is not None
    assert ack.body.result["mode"] == "resume"
    assert [item["seq"] for item in ack.body.result["replay"]] == [15]


@pytest.mark.asyncio
async def test_resume_with_stale_cursor_falls_back_to_snapshots() -> None:
    broker = _HydrateBroker(watermark=20, retained_floor=10)
    transport = _RecordingTransport()

    await _run_hydrate_once(_server(broker), _session(transport), _hydrate_message(cursor=3))

    assert broker.replay_calls == []
    assert broker.request_calls == HYDRATE_ALL_REQUESTS
    assert broker.tail_calls == [
        (20, "state.snapshot"),
        (20, "conversation.block"),
        (20, "conversation.state"),
        (20, "error"),
    ]
    ack = _hydrate_ack(transport)
    assert ack.body.result is not None
    assert ack.body.result["mode"] == "snapshot_fallback"
