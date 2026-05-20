from __future__ import annotations

import asyncio
import contextlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from murder.bus.broker import DurableBroker
from murder.bus.protocol import (
    PRESENCE_DISCONNECT_DEBOUNCE_S,
    PRESENCE_USER_KINDS,
    PROTOCOL_VERSION,
    SOCKET_BASENAME,
    SOCKET_RUNTIME_SUBDIR,
    WIRE_MESSAGE_ADAPTER,
    AckBody,
    AckMessage,
    ClientKind,
    Entity,
    ErrBody,
    ErrMessage,
    HelloMessage,
    PresenceEvent,
    PresenceState,
    PubMessage,
    RpcMessage,
    SubMessage,
    WakeBody,
    WakeMessage,
)


@dataclass
class _ClientSession:
    client_id: str
    kind: ClientKind
    transport: UdsTransport
    subscriptions: set[asyncio.Task[None]] = field(default_factory=set)


class SocketBusServer:
    def __init__(
        self,
        broker: DurableBroker,
        *,
        run_id: str,
        socket_path: Path | None = None,
        disconnect_debounce_s: float = PRESENCE_DISCONNECT_DEBOUNCE_S,
    ) -> None:
        self._broker = broker
        self._run_id = run_id
        self._socket_path = socket_path or default_socket_path()
        self._disconnect_debounce_s = disconnect_debounce_s
        self._server: asyncio.AbstractServer | None = None
        self._clients: dict[int, _ClientSession] = {}
        self._presence_state = PresenceState.HEADLESS
        self._presence_version = 0
        self._presence_task: asyncio.Task[None] | None = None
        self._kind_counts: dict[ClientKind, int] = {}
        self._closed = False

    @property
    def socket_path(self) -> Path:
        return self._socket_path

    async def start(self) -> None:
        self._socket_path.parent.mkdir(parents=True, exist_ok=True)
        if self._socket_path.exists():
            with contextlib.suppress(FileNotFoundError, OSError):
                self._socket_path.unlink()
        self._server = await asyncio.start_unix_server(
            self._handle_client,
            path=str(self._socket_path),
        )

    async def stop(self) -> None:
        self._closed = True
        if self._presence_task is not None:
            self._presence_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._presence_task
        for session in list(self._clients.values()):
            for task in list(session.subscriptions):
                task.cancel()
            await session.transport.close()
        self._clients.clear()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        if self._socket_path.exists():
            self._socket_path.unlink()

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        transport = attach_stream_transport(reader, writer)
        session_key = id(transport)
        session: _ClientSession | None = None
        try:
            hello = await self._read_hello(reader)
            if hello.body.protocol_version != PROTOCOL_VERSION:
                await self._send_err(
                    transport,
                    correlation_id=hello.correlation_id,
                    code="protocol_version_mismatch",
                    message=(f"server={PROTOCOL_VERSION} client={hello.body.protocol_version}"),
                )
                return
            session = _ClientSession(
                client_id=hello.body.client_id,
                kind=hello.body.client_kind,
                transport=transport,
            )
            self._clients[session_key] = session
            await self._send_ack(
                transport,
                correlation_id=hello.correlation_id,
                kind="subscribed",
            )
            await self._send_wake(transport, hello.body.client_id)
            await self._on_connect(hello.body.client_kind)
            while not reader.at_eof():
                line = await reader.readline()
                if not line:
                    break
                msg = WIRE_MESSAGE_ADAPTER.validate_json(line.decode("utf-8"))
                if isinstance(msg, SubMessage):
                    if session is None:
                        continue
                    task = asyncio.create_task(
                        self._run_subscription(session, msg),
                        name=f"bus-sub:{msg.correlation_id}",
                    )
                    session.subscriptions.add(task)
                    task.add_done_callback(session.subscriptions.discard)
                    continue
                if isinstance(msg, PubMessage):
                    await self._broker.publish(msg.event)
                    await self._send_ack(
                        transport,
                        correlation_id=msg.correlation_id,
                        kind="pong",
                    )
                    continue
                if isinstance(msg, RpcMessage):
                    await self._handle_rpc(transport, msg)
                    continue
                await self._send_err(
                    transport,
                    correlation_id=msg.correlation_id,
                    code="unsupported_op",
                    message=f"unsupported op {msg.op}",
                )
        except Exception as exc:  # noqa: BLE001
            if not self._closed:
                with contextlib.suppress(Exception):
                    await self._send_err(
                        transport,
                        correlation_id="",
                        code="server_error",
                        message=str(exc),
                    )
        finally:
            if session is not None:
                for task in list(session.subscriptions):
                    task.cancel()
                self._clients.pop(session_key, None)
                await self._on_disconnect(session.kind)
            await transport.close()

    async def _read_hello(self, reader: asyncio.StreamReader) -> HelloMessage:
        raw = await reader.readline()
        if not raw:
            raise RuntimeError("client disconnected before hello")
        msg = WIRE_MESSAGE_ADAPTER.validate_json(raw.decode("utf-8"))
        if not isinstance(msg, HelloMessage):
            raise RuntimeError("first message must be hello")
        return msg

    async def _handle_rpc(self, transport: UdsTransport, msg: RpcMessage) -> None:
        try:
            result = await self._broker.request(
                msg.args.target,
                msg.args.body,
                timeout_s=msg.args.timeout_s,
            )
        except Exception as exc:  # noqa: BLE001
            await self._send_err(
                transport,
                correlation_id=msg.correlation_id,
                code="rpc_error",
                message=str(exc),
            )
            return
        await self._send_ack(
            transport,
            correlation_id=msg.correlation_id,
            kind="rpc_reply",
            result=result,
        )

    async def _run_subscription(self, session: _ClientSession, msg: SubMessage) -> None:
        filt = msg.args.filter
        watermark = self._broker.watermark()
        transport = session.transport
        await self._send_ack(
            transport,
            correlation_id=msg.correlation_id,
            kind="subscribed",
        )
        for _, event in self._broker.replay(
            filt,
            since_id=msg.args.since_id or 0,
            until_id=watermark,
        ):
            await self._send_pub(transport, msg.correlation_id, event)
        await self._send_ack(
            transport,
            correlation_id=msg.correlation_id,
            kind="replay_done",
            watermark=watermark,
        )
        if msg.args.presence_retain:
            retained = self._presence_event()
            if filt.matches(retained):
                await self._send_pub(transport, msg.correlation_id, retained)
        async for _, event in self._broker.tail(filt, since_id=watermark):
            await self._send_pub(transport, msg.correlation_id, event)

    async def _send_pub(
        self,
        transport: UdsTransport,
        correlation_id: str,
        event: Any,
    ) -> None:
        await self._send_message(
            transport,
            PubMessage(correlation_id=correlation_id, event=event),
        )

    async def _send_ack(
        self,
        transport: UdsTransport,
        *,
        correlation_id: str,
        kind: str,
        watermark: int | None = None,
        result: dict[str, Any] | None = None,
    ) -> None:
        await self._send_message(
            transport,
            AckMessage(
                correlation_id=correlation_id,
                body=AckBody(kind=kind, watermark=watermark, result=result),
            ),
        )

    async def _send_err(
        self,
        transport: UdsTransport,
        *,
        correlation_id: str,
        code: str,
        message: str,
    ) -> None:
        await self._send_message(
            transport,
            ErrMessage(
                correlation_id=correlation_id,
                body=ErrBody(code=code, message=message),
            ),
        )

    async def _send_wake(self, transport: UdsTransport, client_id: str) -> None:
        hints = [
            Entity.TICKET,
            Entity.AGENT,
            Entity.PLAN,
            Entity.NOTE,
            Entity.ESCALATION,
            Entity.QUEUE_ROW,
            Entity.SENTINEL_STATE,
        ]
        await self._send_message(
            transport,
            WakeMessage(
                correlation_id="",
                body=WakeBody(client_id=client_id, reason="connect", fresh_state_hints=hints),
            ),
        )

    async def _send_message(self, transport: UdsTransport, message: Any) -> None:
        wire = message.model_dump(mode="json")
        await transport.send((json.dumps(wire, default=str) + "\n").encode("utf-8"))

    async def _on_connect(self, kind: ClientKind) -> None:
        self._kind_counts[kind] = self._kind_counts.get(kind, 0) + 1
        if self._presence_task is not None:
            self._presence_task.cancel()
            self._presence_task = None
        if kind in PRESENCE_USER_KINDS and self._user_count() > 0:
            await self._publish_presence(PresenceState.ATTENDED)

    async def _on_disconnect(self, kind: ClientKind) -> None:
        current = self._kind_counts.get(kind, 0)
        if current <= 1:
            self._kind_counts.pop(kind, None)
        else:
            self._kind_counts[kind] = current - 1
        if self._user_count() == 0 and self._presence_task is None:
            self._presence_task = asyncio.create_task(self._debounced_headless())

    async def _debounced_headless(self) -> None:
        try:
            await asyncio.sleep(self._disconnect_debounce_s)
            if self._user_count() == 0:
                await self._publish_presence(PresenceState.HEADLESS)
        finally:
            self._presence_task = None

    async def _publish_presence(self, state: PresenceState) -> None:
        if self._presence_state == state:
            return
        self._presence_state = state
        self._presence_version += 1
        await self._broker.publish(self._presence_event())

    def _presence_event(self) -> PresenceEvent:
        kinds = {kind.value: count for kind, count in self._kind_counts.items() if count > 0}
        return PresenceEvent(
            run_id=self._run_id,
            agent_id="supervisor",
            state=self._presence_state,
            user_count=self._user_count(),
            kinds=kinds,
            version=self._presence_version,
        )

    def _user_count(self) -> int:
        return sum(
            count for kind, count in self._kind_counts.items() if kind in PRESENCE_USER_KINDS
        )


def default_socket_path() -> Path:
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_dir:
        return Path(runtime_dir) / SOCKET_RUNTIME_SUBDIR / SOCKET_BASENAME
    return Path(f"/tmp/murder-{os.getuid()}") / SOCKET_BASENAME


# ---------------------------------------------------------------------------
# Client-side UDS Transport
# ---------------------------------------------------------------------------

from murder.bus.transport import Transport  # noqa: E402


_SENTINEL = object()  # signals the writer drain loop to stop


class UdsTransport(Transport):
    """Async client-side Unix-domain-socket transport.

    Design choices
    --------------
    * **Single-writer queue** — all outbound bytes (subscription pushes,
      RPC replies, acks) go through a single ``asyncio.Queue``.  A
      dedicated ``_drain_loop`` task is the sole coroutine that calls
      ``writer.write`` / ``writer.drain``.  This prevents interleaving
      when multiple coroutines write concurrently on the same
      ``StreamWriter``.

    * **Two distinct timeouts**:
      - ``rpc_timeout`` — applied to short request/response exchanges
        (handshake, RPC, ack).  Default 30 s.
      - ``subscription_idle_timeout`` — applied to quiet subscription
        streams.  If no data arrives for this long the connection is
        closed.  Default 300 s.

    * **Backpressure** — the queue has a bounded capacity
      (``_WRITE_QUEUE_MAX``).  If a caller calls ``send()`` when the queue
      is full the call raises ``TransportWriteQueueFullError`` immediately
      rather than blocking indefinitely.  Callers must handle this and
      decide whether to retry or drop.
    """

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

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(self, path: str | Path) -> None:
        """Open a connection to the UDS at *path*."""
        reader, writer = await asyncio.open_unix_connection(str(path))
        self._reader = reader
        self._writer = writer
        self._connected = True
        self._drain_task = asyncio.create_task(
            self._drain_loop(), name="uds-transport-drain"
        )

    async def _drain_loop(self) -> None:
        """Single writer coroutine — serialises all outbound writes."""
        assert self._writer is not None
        writer = self._writer
        try:
            while True:
                item = await self._write_queue.get()
                if item is _SENTINEL:
                    break
                assert isinstance(item, bytes)
                try:
                    writer.write(item)
                    await writer.drain()
                except Exception:
                    self._connected = False
                    raise
        finally:
            self._connected = False

    # ------------------------------------------------------------------
    # Transport ABC
    # ------------------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def send(self, data: bytes) -> None:
        """Enqueue *data* for the drain loop.

        Raises ``TransportWriteQueueFullError`` immediately if the queue
        is at capacity (backpressure: drop-with-error, not silent drop or
        unbounded block).
        """
        if not self._connected:
            raise TransportClosedError("transport is not connected")
        try:
            self._write_queue.put_nowait(data)
        except asyncio.QueueFull as exc:
            raise TransportWriteQueueFullError(
                f"write queue full ({self._WRITE_QUEUE_MAX} items)"
            ) from exc

    async def recv(self) -> bytes:
        """Read the next chunk from the remote end.

        Returns ``b""`` on clean EOF.
        Uses ``subscription_idle_timeout``; raises ``asyncio.TimeoutError``
        if no data arrives within that window.
        """
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
            # EOF — peer closed
            self._connected = False
        return data

    async def close(self) -> None:
        """Close the connection and stop the drain loop."""
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


def attach_stream_transport(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    subscription_idle_timeout: float = 300.0,
) -> UdsTransport:
    """Attach an accepted asyncio stream pair to ``UdsTransport`` (server-side)."""
    transport = UdsTransport(subscription_idle_timeout=subscription_idle_timeout)
    transport._reader = reader
    transport._writer = writer
    transport._connected = True
    transport._drain_task = asyncio.create_task(
        transport._drain_loop(), name="uds-stream-transport-drain"
    )
    return transport


# ---------------------------------------------------------------------------
# Transport-layer exceptions
# ---------------------------------------------------------------------------


class TransportError(RuntimeError):
    """Base class for UdsTransport errors."""


class TransportClosedError(TransportError):
    """Raised when an operation is attempted on a closed transport."""


class TransportWriteQueueFullError(TransportError):
    """Raised when the outbound write queue is at capacity.

    Policy: **error on full** — callers receive an exception immediately
    rather than blocking or silently dropping.  This makes backpressure
    visible to the layer above, which can decide to retry, shed load, or
    propagate the error.
    """


__all__ = [
    "UdsTransport",
    "attach_stream_transport",
    "TransportError",
    "TransportClosedError",
    "TransportWriteQueueFullError",
]
