from __future__ import annotations

import asyncio
import contextlib
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


class SocketBusClient(BusBroker):
    """Small JSON-lines client for the local supervisor socket.

    RPC and publish calls use short-lived connections. Subscriptions keep a
    dedicated connection open for the iterator lifetime.
    """

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
        reader, writer = await self._connect()
        try:
            correlation_id = f"pub-{uuid4().hex}"
            await self._send(writer, PubMessage(correlation_id=correlation_id, event=event))
            msg = await self._recv(reader)
            if isinstance(msg, ErrMessage):
                raise RuntimeError(msg.body.message)
            if not isinstance(msg, AckMessage) or msg.correlation_id != correlation_id:
                raise RuntimeError("unexpected publish acknowledgement")
        finally:
            await self._close(writer)

    async def subscribe(
        self,
        filter: EventFilter | None = None,
        *,
        since_id: int | None = None,
    ) -> AsyncIterator[BusEvent]:
        reader, writer = await self._connect()
        try:
            correlation_id = f"sub-{uuid4().hex}"
            await self._send(
                writer,
                SubMessage(
                    correlation_id=correlation_id,
                    args=SubArgs(filter=filter or EventFilter(), since_id=since_id),
                ),
            )
            while True:
                msg = await self._recv(reader)
                if isinstance(msg, ErrMessage):
                    raise RuntimeError(msg.body.message)
                if isinstance(msg, WakeMessage):
                    continue
                if isinstance(msg, AckMessage):
                    continue
                if isinstance(msg, PubMessage):
                    yield msg.event
        finally:
            await self._close(writer)

    async def request(
        self,
        target: str,
        body: dict,
        *,
        timeout_s: float,
    ) -> dict:
        reader, writer = await self._connect()
        try:
            correlation_id = f"rpc-{uuid4().hex}"
            await self._send(
                writer,
                RpcMessage(
                    correlation_id=correlation_id,
                    args=RpcArgs(target=target, body=body, timeout_s=timeout_s),
                ),
            )
            while True:
                msg = await self._recv(reader, timeout_s=timeout_s + 1.0)
                if isinstance(msg, WakeMessage):
                    continue
                if isinstance(msg, ErrMessage):
                    raise RuntimeError(msg.body.message)
                if isinstance(msg, AckMessage) and msg.correlation_id == correlation_id:
                    return msg.body.result or {}
        finally:
            await self._close(writer)

    async def _connect(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        reader, writer = await asyncio.open_unix_connection(str(self.socket_path))
        correlation_id = f"hello-{uuid4().hex}"
        await self._send(
            writer,
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
            msg = await self._recv(reader)
            if isinstance(msg, WakeMessage):
                continue
            if isinstance(msg, ErrMessage):
                raise RuntimeError(msg.body.message)
            if isinstance(msg, AckMessage) and msg.correlation_id == correlation_id:
                return reader, writer

    async def _send(self, writer: asyncio.StreamWriter, message: object) -> None:
        writer.write((json.dumps(message.model_dump(mode="json"), default=str) + "\n").encode())
        await writer.drain()

    async def _recv(
        self,
        reader: asyncio.StreamReader,
        *,
        timeout_s: float = 5.0,
    ) -> object:
        line = await asyncio.wait_for(reader.readline(), timeout=timeout_s)
        if not line:
            raise RuntimeError("bus socket closed")
        return WIRE_MESSAGE_ADAPTER.validate_json(line.decode("utf-8"))

    async def _close(self, writer: asyncio.StreamWriter) -> None:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
