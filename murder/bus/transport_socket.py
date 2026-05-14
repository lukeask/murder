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
    writer: asyncio.StreamWriter
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
            session.writer.close()
            with contextlib.suppress(Exception):
                await session.writer.wait_closed()
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
        session_key = id(writer)
        session: _ClientSession | None = None
        try:
            hello = await self._read_hello(reader)
            if hello.body.protocol_version != PROTOCOL_VERSION:
                await self._send_err(
                    writer,
                    correlation_id=hello.correlation_id,
                    code="protocol_version_mismatch",
                    message=(
                        f"server={PROTOCOL_VERSION} client={hello.body.protocol_version}"
                    ),
                )
                return
            session = _ClientSession(
                client_id=hello.body.client_id,
                kind=hello.body.client_kind,
                writer=writer,
            )
            self._clients[session_key] = session
            await self._send_ack(
                writer,
                correlation_id=hello.correlation_id,
                kind="subscribed",
            )
            await self._send_wake(writer, hello.body.client_id)
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
                        writer,
                        correlation_id=msg.correlation_id,
                        kind="pong",
                    )
                    continue
                if isinstance(msg, RpcMessage):
                    await self._handle_rpc(writer, msg)
                    continue
                await self._send_err(
                    writer,
                    correlation_id=msg.correlation_id,
                    code="unsupported_op",
                    message=f"unsupported op {msg.op}",
                )
        except Exception as exc:  # noqa: BLE001
            if not self._closed:
                with contextlib.suppress(Exception):
                    await self._send_err(
                        writer,
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
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def _read_hello(self, reader: asyncio.StreamReader) -> HelloMessage:
        raw = await reader.readline()
        if not raw:
            raise RuntimeError("client disconnected before hello")
        msg = WIRE_MESSAGE_ADAPTER.validate_json(raw.decode("utf-8"))
        if not isinstance(msg, HelloMessage):
            raise RuntimeError("first message must be hello")
        return msg

    async def _handle_rpc(self, writer: asyncio.StreamWriter, msg: RpcMessage) -> None:
        try:
            result = await self._broker.request(
                msg.args.target,
                msg.args.body,
                timeout_s=msg.args.timeout_s,
            )
        except Exception as exc:  # noqa: BLE001
            await self._send_err(
                writer,
                correlation_id=msg.correlation_id,
                code="rpc_error",
                message=str(exc),
            )
            return
        await self._send_ack(
            writer,
            correlation_id=msg.correlation_id,
            kind="rpc_reply",
            result=result,
        )

    async def _run_subscription(self, session: _ClientSession, msg: SubMessage) -> None:
        filt = msg.args.filter
        watermark = self._broker.watermark()
        await self._send_ack(
            session.writer,
            correlation_id=msg.correlation_id,
            kind="subscribed",
        )
        for _, event in self._broker.replay(
            filt,
            since_id=msg.args.since_id or 0,
            until_id=watermark,
        ):
            await self._send_pub(session.writer, msg.correlation_id, event)
        await self._send_ack(
            session.writer,
            correlation_id=msg.correlation_id,
            kind="replay_done",
            watermark=watermark,
        )
        if msg.args.presence_retain:
            retained = self._presence_event()
            if filt.matches(retained):
                await self._send_pub(session.writer, msg.correlation_id, retained)
        async for _, event in self._broker.tail(filt, since_id=watermark):
            await self._send_pub(session.writer, msg.correlation_id, event)

    async def _send_pub(
        self,
        writer: asyncio.StreamWriter,
        correlation_id: str,
        event: Any,
    ) -> None:
        await self._send_message(
            writer,
            PubMessage(correlation_id=correlation_id, event=event),
        )

    async def _send_ack(
        self,
        writer: asyncio.StreamWriter,
        *,
        correlation_id: str,
        kind: str,
        watermark: int | None = None,
        result: dict[str, Any] | None = None,
    ) -> None:
        await self._send_message(
            writer,
            AckMessage(
                correlation_id=correlation_id,
                body=AckBody(kind=kind, watermark=watermark, result=result),
            ),
        )

    async def _send_err(
        self,
        writer: asyncio.StreamWriter,
        *,
        correlation_id: str,
        code: str,
        message: str,
    ) -> None:
        await self._send_message(
            writer,
            ErrMessage(
                correlation_id=correlation_id,
                body=ErrBody(code=code, message=message),
            ),
        )

    async def _send_wake(self, writer: asyncio.StreamWriter, client_id: str) -> None:
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
            writer,
            WakeMessage(
                correlation_id="",
                body=WakeBody(client_id=client_id, reason="connect", fresh_state_hints=hints),
            ),
        )

    async def _send_message(self, writer: asyncio.StreamWriter, message: Any) -> None:
        wire = message.model_dump(mode="json")
        writer.write((json.dumps(wire, default=str) + "\n").encode("utf-8"))
        await writer.drain()

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
            count
            for kind, count in self._kind_counts.items()
            if kind in PRESENCE_USER_KINDS
        )


def default_socket_path() -> Path:
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_dir:
        return Path(runtime_dir) / SOCKET_RUNTIME_SUBDIR / SOCKET_BASENAME
    return Path(f"/tmp/murder-{os.getuid()}") / SOCKET_BASENAME
