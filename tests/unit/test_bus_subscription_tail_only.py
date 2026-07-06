"""Tail-only subscriptions skip historical replay (error-toast boot noise fix)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from murder.bus.protocol import ClientKind, PROTOCOL_VERSION, SubMessage, WIRE_MESSAGE_ADAPTER
from murder.bus.transport_socket import SocketBusServer, _ClientSession


class _RecordingBroker:
    """Minimal broker double that records replay cursors."""

    def __init__(self, *, watermark: int = 42) -> None:
        self._watermark = watermark
        self.replay_since_ids: list[int] = []

    def watermark(self) -> int:
        return self._watermark

    def replay(
        self,
        filter: Any = None,
        *,
        since_id: int,
        until_id: int | None = None,
    ) -> list[tuple[int, Any]]:
        self.replay_since_ids.append(since_id)
        return []

    async def tail(self, filter: Any = None, *, since_id: int):
        while True:
            await asyncio.sleep(3600)
            yield  # pragma: no cover — cancelled in tests


class _RecordingTransport:
    def __init__(self) -> None:
        self.sent: list[bytes] = []

    async def send(self, data: bytes) -> None:
        self.sent.append(data)


def _sub_message(*, tail_only: bool) -> SubMessage:
    raw: dict[str, Any] = {
        "op": "sub",
        "schema_version": PROTOCOL_VERSION,
        "correlation_id": "sub-test",
        "args": {
            "filter": {"type": "error"},
            "tail_only": tail_only,
            "presence_retain": False,
        },
    }
    msg = WIRE_MESSAGE_ADAPTER.validate_python(raw)
    assert isinstance(msg, SubMessage)
    return msg


def _server(broker: _RecordingBroker) -> SocketBusServer:
    return SocketBusServer(
        broker,  # type: ignore[arg-type]
        run_id="run-test",
        socket_path=Path("/tmp/nonexistent-tail-only-test.sock"),
    )


@pytest.mark.asyncio
async def test_tail_only_subscription_replays_from_watermark_not_zero() -> None:
    broker = _RecordingBroker(watermark=99)
    server = _server(broker)
    session = _ClientSession(
        client_id="tui-test",
        kind=ClientKind.TUI,
        transport=_RecordingTransport(),  # type: ignore[arg-type]
    )
    task = asyncio.create_task(
        server._run_subscription(session, _sub_message(tail_only=True)),
    )
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert broker.replay_since_ids == [99]


@pytest.mark.asyncio
async def test_default_subscription_replays_from_zero() -> None:
    broker = _RecordingBroker(watermark=99)
    server = _server(broker)
    session = _ClientSession(
        client_id="tui-test",
        kind=ClientKind.TUI,
        transport=_RecordingTransport(),  # type: ignore[arg-type]
    )
    task = asyncio.create_task(
        server._run_subscription(session, _sub_message(tail_only=False)),
    )
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert broker.replay_since_ids == [0]
