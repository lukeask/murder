"""Optional TCP byte transport (plain TCP, no TLS/auth)."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable

from murder.bus.transport import Transport
from murder.bus.transport_socket import TransportClosedError, TransportWriteQueueFullError

_SENTINEL = object()


class TcpTransport(Transport):
    """Async client-side TCP transport with a single-writer drain loop."""

    _WRITE_QUEUE_MAX = 256

    def __init__(
        self,
        *,
        rpc_timeout: float = 30.0,
        subscription_idle_timeout: float = 300.0,
    ) -> None:
        self.rpc_timeout = rpc_timeout
        self.subscription_idle_timeout = subscription_idle_timeout
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._write_queue: asyncio.Queue[bytes | object] = asyncio.Queue(
            maxsize=self._WRITE_QUEUE_MAX
        )
        self._drain_task: asyncio.Task[None] | None = None
        self._connected = False

    async def connect(self, host: str, port: int) -> None:
        reader, writer = await asyncio.open_connection(host, port)
        self._reader = reader
        self._writer = writer
        self._connected = True
        self._drain_task = asyncio.create_task(self._drain_loop(), name="tcp-transport-drain")

    async def _drain_loop(self) -> None:
        assert self._writer is not None
        writer = self._writer
        try:
            while True:
                item = await self._write_queue.get()
                if item is _SENTINEL:
                    break
                assert isinstance(item, bytes)
                writer.write(item)
                await writer.drain()
        except Exception:
            self._connected = False
            raise
        finally:
            self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def send(self, data: bytes) -> None:
        if not self._connected:
            raise TransportClosedError("transport is not connected")
        try:
            self._write_queue.put_nowait(data)
        except asyncio.QueueFull as exc:
            raise TransportWriteQueueFullError(
                f"write queue full ({self._WRITE_QUEUE_MAX} items)"
            ) from exc

    async def recv(self) -> bytes:
        if self._reader is None:
            raise TransportClosedError("transport is not connected")
        try:
            data = await asyncio.wait_for(
                self._reader.read(65536),
                timeout=self.subscription_idle_timeout,
            )
        except asyncio.TimeoutError:
            await self.close()
            raise
        if data == b"":
            self._connected = False
        return data

    async def close(self) -> None:
        self._connected = False
        if self._drain_task is not None and not self._drain_task.done():
            with contextlib.suppress(asyncio.QueueFull):
                self._write_queue.put_nowait(_SENTINEL)
            self._drain_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._drain_task
            self._drain_task = None
        if self._writer is not None:
            self._writer.close()
            with contextlib.suppress(Exception):
                await self._writer.wait_closed()
            self._writer = None
        self._reader = None


async def start_tcp_server(
    client_connected: Callable[
        [asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]
    ],
    *,
    host: str = "127.0.0.1",
    port: int = 0,
) -> tuple[asyncio.AbstractServer, str, int]:
    server = await asyncio.start_server(client_connected, host, port)
    sockets = server.sockets
    if not sockets:
        raise RuntimeError("TCP server started without bound sockets")
    bound = sockets[0].getsockname()
    return server, str(bound[0]), int(bound[1])


__all__ = [
    "TcpTransport",
    "start_tcp_server",
]
