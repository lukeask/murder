from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

from murder.bus.broker import BusBroker
from murder.bus.protocol import (
    PROTOCOL_VERSION,
    WIRE_MESSAGE_ADAPTER,
    AckMessage,
    BusEvent,
    ClientKind,
    ErrMessage,
    EventFilter,
    HelloBody,
    HelloMessage,
    PubMessage,
    RpcArgs,
    RpcMessage,
    SubArgs,
    SubMessage,
    WakeMessage,
)

from .transport_socket import UdsTransport


class _UdsJsonSession:
    """JSON-lines framing over a connected ``UdsTransport``."""

    def __init__(self, transport: UdsTransport) -> None:
        self._transport = transport
        self._buf = bytearray()

    async def close(self) -> None:
        await self._transport.close()

    async def send(self, message: object) -> None:
        payload = json.dumps(message.model_dump(mode="json"), default=str) + "\n"
        await self._transport.send(payload.encode())

    async def recv(self, *, timeout_s: float) -> object:
        line = await self._readline(timeout_s=timeout_s)
        return WIRE_MESSAGE_ADAPTER.validate_json(line.decode("utf-8"))

    async def _readline(self, *, timeout_s: float) -> bytes:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        while True:
            nl = self._buf.find(b"\n")
            if nl >= 0:
                line = bytes(self._buf[: nl + 1])
                del self._buf[: nl + 1]
                return line
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise asyncio.TimeoutError()
            chunk = await asyncio.wait_for(self._transport.recv(), timeout=remaining)
            if not chunk:
                raise RuntimeError("bus socket closed")
            self._buf.extend(chunk)


class SocketBusClient(BusBroker):
    """Small JSON-lines client for the local supervisor socket.

    RPC and publish calls use short-lived connections. Subscriptions keep a
    dedicated connection open for the iterator lifetime.
    """

    _RPC_IDLE_TIMEOUT_S = 5.0
    _SUBSCRIPTION_IDLE_TIMEOUT_S = 300.0

    def __init__(
        self,
        socket_path: Path,
        *,
        client_kind: ClientKind = ClientKind.CLI_EPHEMERAL,
        client_id: str | None = None,
    ) -> None:
        self.socket_path = socket_path
        self.client_kind = client_kind
        self.client_id = client_id or f"{client_kind.value}-{uuid4().hex}"

    async def publish(self, event: BusEvent) -> None:
        session = await self._connect(idle_timeout_s=self._RPC_IDLE_TIMEOUT_S)
        try:
            correlation_id = f"pub-{uuid4().hex}"
            await session.send(PubMessage(correlation_id=correlation_id, event=event))
            msg = await session.recv(timeout_s=self._RPC_IDLE_TIMEOUT_S)
            if isinstance(msg, ErrMessage):
                raise RuntimeError(msg.body.message)
            if not isinstance(msg, AckMessage) or msg.correlation_id != correlation_id:
                raise RuntimeError("unexpected publish acknowledgement")
        finally:
            await session.close()

    async def subscribe(
        self,
        filter: EventFilter | None = None,
        *,
        since_id: int | None = None,
    ) -> AsyncIterator[BusEvent]:
        session = await self._connect(idle_timeout_s=self._SUBSCRIPTION_IDLE_TIMEOUT_S)
        try:
            correlation_id = f"sub-{uuid4().hex}"
            await session.send(
                SubMessage(
                    correlation_id=correlation_id,
                    args=SubArgs(filter=filter or EventFilter(), since_id=since_id),
                ),
            )
            while True:
                msg = await session.recv(timeout_s=self._SUBSCRIPTION_IDLE_TIMEOUT_S)
                if isinstance(msg, ErrMessage):
                    raise RuntimeError(msg.body.message)
                if isinstance(msg, WakeMessage):
                    continue
                if isinstance(msg, AckMessage):
                    continue
                if isinstance(msg, PubMessage):
                    yield msg.event
        finally:
            await session.close()

    async def request(
        self,
        target: str,
        body: dict,
        *,
        timeout_s: float,
    ) -> dict:
        session = await self._connect(idle_timeout_s=self._RPC_IDLE_TIMEOUT_S)
        recv_timeout = timeout_s + 1.0
        try:
            correlation_id = f"rpc-{uuid4().hex}"
            await session.send(
                RpcMessage(
                    correlation_id=correlation_id,
                    args=RpcArgs(target=target, body=body, timeout_s=timeout_s),
                ),
            )
            while True:
                msg = await session.recv(timeout_s=recv_timeout)
                if isinstance(msg, WakeMessage):
                    continue
                if isinstance(msg, ErrMessage):
                    raise RuntimeError(msg.body.message)
                if isinstance(msg, AckMessage) and msg.correlation_id == correlation_id:
                    return msg.body.result or {}
        finally:
            await session.close()

    async def _connect(self, *, idle_timeout_s: float) -> _UdsJsonSession:
        transport = UdsTransport(subscription_idle_timeout=idle_timeout_s)
        await transport.connect(self.socket_path)
        session = _UdsJsonSession(transport)
        correlation_id = f"hello-{uuid4().hex}"
        await session.send(
            HelloMessage(
                correlation_id=correlation_id,
                body=HelloBody(
                    protocol_version=PROTOCOL_VERSION,
                    client_kind=self.client_kind,
                    client_id=self.client_id,
                ),
            ),
        )
        while True:
            msg = await session.recv(timeout_s=idle_timeout_s)
            if isinstance(msg, WakeMessage):
                continue
            if isinstance(msg, ErrMessage):
                raise RuntimeError(msg.body.message)
            if isinstance(msg, AckMessage) and msg.correlation_id == correlation_id:
                return session
