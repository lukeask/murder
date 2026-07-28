"""Focused validation and ordering coverage for ``terminal.input``."""

from __future__ import annotations

import asyncio
import base64
from uuid import UUID, uuid4

import pytest

from murder.app.protocol.wire import TerminalInputMessage
from murder.app.service.socket_server import (
    ApplicationConnection,
    TerminalInputCoordinator,
)

_STALE_FENCE = 9


class _Socket:
    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []

    async def send_json(self, payload: dict[str, object]) -> None:
        self.sent.append(payload)

    async def close(self) -> None:
        return None


def _message(
    *,
    stream_id: str,
    session_id: UUID,
    sequence: int,
    data: bytes,
    lease_id: UUID | None = None,
    fence: int = 1,
) -> TerminalInputMessage:
    return TerminalInputMessage(
        stream_id=stream_id,
        session_id=session_id,
        lease_id=lease_id or uuid4(),
        fence=fence,
        input_sequence=sequence,
        data=base64.b64encode(data).decode("ascii"),
    )


async def _eventually(predicate: object) -> None:
    for _ in range(50):
        if callable(predicate) and predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition did not become true")


@pytest.mark.asyncio
async def test_terminal_input_preserves_controls_utf8_escapes_and_order() -> None:
    writes: list[bytes] = []
    validations: list[tuple[UUID, str, UUID, int]] = []
    session_id = uuid4()
    lease_id = uuid4()

    async def validate(session: UUID, client: str, lease: UUID, fence: int) -> None:
        validations.append((session, client, lease, fence))

    async def write(_session: UUID, _client: str, _lease: UUID, _fence: int, data: bytes) -> None:
        writes.append(data)

    socket = _Socket()
    connection = ApplicationConnection(socket, "client-a")
    coordinator = TerminalInputCoordinator(write, validate)
    first = b"i\x1b\x17\x1b[200~caf\xc3\xa9"
    second = b"\x1b[201~\x1b[1;5D"
    try:
        await coordinator.accept(
            connection,
            _message(
                stream_id="editor-input-1",
                session_id=session_id,
                sequence=1,
                data=first,
                lease_id=lease_id,
            ),
        )
        await coordinator.accept(
            connection,
            _message(
                stream_id="editor-input-1",
                session_id=session_id,
                sequence=2,
                data=second,
                lease_id=lease_id,
            ),
        )
        await _eventually(lambda: writes == [first, second])
        assert validations == [
            (session_id, "client-a", lease_id, 1),
            (session_id, "client-a", lease_id, 1),
        ]
        assert [item["accepted_through"] for item in socket.sent] == [1, 2]
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_terminal_input_rejects_gap_and_stale_writer_before_ack() -> None:
    session_id = uuid4()
    socket = _Socket()
    connection = ApplicationConnection(socket, "client-a")

    async def validate(_session: UUID, _client: str, _lease: UUID, fence: int) -> None:
        if fence == _STALE_FENCE:
            raise ValueError("stale writer lease")

    async def write(*_args: object) -> None:
        return None

    coordinator = TerminalInputCoordinator(write, validate)
    try:
        with pytest.raises(ValueError, match="stale writer lease"):
            await coordinator.accept(
                connection,
                _message(
                    stream_id="s",
                    session_id=session_id,
                    sequence=1,
                    data=b"x",
                    fence=_STALE_FENCE,
                ),
            )
        assert socket.sent == []
        await coordinator.accept(
            connection,
            _message(stream_id="s", session_id=session_id, sequence=1, data=b"x"),
        )
        with pytest.raises(ValueError, match="sequence gap"):
            await coordinator.accept(
                connection,
                _message(stream_id="s", session_id=session_id, sequence=3, data=b"y"),
            )
        assert [item["accepted_through"] for item in socket.sent] == [1]
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_terminal_input_duplicate_is_not_written_twice_and_writes_are_serial() -> None:
    session_id = uuid4()
    started: list[bytes] = []
    release = asyncio.Event()
    socket = _Socket()
    connection = ApplicationConnection(socket, "client-a")

    async def validate(*_args: object) -> None:
        return None

    async def write(_session: UUID, _client: str, _lease: UUID, _fence: int, data: bytes) -> None:
        started.append(data)
        if data == b"first":
            await release.wait()

    coordinator = TerminalInputCoordinator(write, validate)
    try:
        first = _message(stream_id="s", session_id=session_id, sequence=1, data=b"first")
        second = _message(stream_id="s", session_id=session_id, sequence=2, data=b"second")
        await coordinator.accept(connection, first)
        await coordinator.accept(connection, second)
        await _eventually(lambda: started == [b"first"])
        await coordinator.accept(connection, first)
        assert started == [b"first"]
        release.set()
        await _eventually(lambda: started == [b"first", b"second"])
        assert [item["accepted_through"] for item in socket.sent] == [1, 2, 2]
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_terminal_input_queue_is_bounded() -> None:
    session_id = uuid4()
    release = asyncio.Event()
    connection = ApplicationConnection(_Socket(), "client-a")

    async def validate(*_args: object) -> None:
        return None

    async def write(*_args: object) -> None:
        await release.wait()

    coordinator = TerminalInputCoordinator(write, validate)
    try:
        # 128 buffered batches are the hard cap; the next admission must fail
        # rather than allowing an unbounded paste/backpressure queue.
        for sequence in range(1, 129):
            await coordinator.accept(
                connection,
                _message(stream_id="s", session_id=session_id, sequence=sequence, data=b"x"),
            )
        with pytest.raises(RuntimeError, match="queue is full"):
            await coordinator.accept(
                connection,
                _message(stream_id="s", session_id=session_id, sequence=129, data=b"x"),
            )
    finally:
        release.set()
        await connection.close()
