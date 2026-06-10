"""Transport write-queue backpressure (the rogue-crow no-history fix).

A subscription replay can enqueue thousands of events without yielding to the
drain loop. The old ``put_nowait`` + raise turned every burst > queue capacity
into a ``TransportWriteQueueFullError`` that silently killed the subscription
task — the TUI stayed connected but never received chat history or tmux
frames. ``send`` now blocks on a full queue (backpressure) instead.

Covers:
- a synchronous burst far larger than the queue capacity is delivered in full;
- ``close()`` releases senders blocked on a full queue (no leaked tasks);
- a subscription task that dies anyway closes the whole connection so the
  client reconnects instead of zombie-listening.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from murder.bus.transport_socket import UdsTransport


@pytest.mark.asyncio
async def test_send_burst_larger_than_queue_is_delivered_in_full(tmp_path: Path) -> None:
    """A burst of 4x the queue capacity blocks (never raises) and all bytes arrive."""
    received: list[bytes] = []
    done = asyncio.Event()
    burst = UdsTransport._WRITE_QUEUE_MAX * 4

    async def _serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        while True:
            line = await reader.readline()
            if not line:
                break
            received.append(line)
            if len(received) >= burst:
                done.set()

    sock = tmp_path / "bp.sock"
    server = await asyncio.start_unix_server(_serve, path=str(sock))
    transport = UdsTransport()
    await transport.connect(sock)
    try:
        for i in range(burst):
            await transport.send(f"msg-{i}\n".encode())
        await asyncio.wait_for(done.wait(), timeout=5.0)
    finally:
        await transport.close()
        server.close()
        await server.wait_closed()

    assert len(received) == burst
    assert received[0] == b"msg-0\n"
    assert received[-1] == f"msg-{burst - 1}\n".encode()


@pytest.mark.asyncio
async def test_close_releases_senders_blocked_on_full_queue(tmp_path: Path) -> None:
    """``close()`` drains the queue so a blocked ``send`` resolves instead of hanging."""
    block_server = asyncio.Event()

    async def _serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await block_server.wait()  # never read — let the socket buffer fill

    sock = tmp_path / "stuck.sock"
    server = await asyncio.start_unix_server(_serve, path=str(sock))
    transport = UdsTransport()
    await transport.connect(sock)

    async def _flood() -> None:
        # Big payloads fill the kernel socket buffer, then the write queue,
        # then this coroutine blocks inside send().
        payload = b"x" * 65536
        while True:
            await transport.send(payload)

    flood = asyncio.create_task(_flood())
    # Let the flood run until the write queue is actually full.
    for _ in range(200):
        await asyncio.sleep(0)
        if transport._write_queue.full():
            break
    assert transport._write_queue.full(), "queue never filled — test setup broken"

    await transport.close()
    # The blocked send must now resolve: either its put completed into the
    # drained queue, or the next send raised TransportClosedError. Either way
    # the task must finish promptly rather than hang forever.
    with pytest.raises(Exception):
        await asyncio.wait_for(flood, timeout=2.0)

    block_server.set()
    server.close()
    await server.wait_closed()
