"""The service's single application-only WebSocket boundary.

No Unix socket, bus envelope, generic publish, or RPC target is accepted here.
The connection class owns one peer, while the two coordinators own the only
long-running application streams.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from murder.app.protocol.common import APPLICATION_PROTOCOL_VERSION, ErrorCode
from murder.app.protocol.projections import validate_event, validate_snapshot
from murder.app.protocol.subscriptions import (
    FactSubscription,
    ProjectionSubscription,
    SubscriptionSnapshot,
)
from murder.app.protocol.terminal import TerminalFrame, TerminalTarget
from murder.app.protocol.wire import (
    APPLICATION_WIRE_ADAPTER,
    ClientHello,
    ErrorMessage,
    ReplyMessage,
    RequestMessage,
    ServerHello,
    SubscribeMessage,
    SubscriptionEventMessage,
    SubscriptionReadyMessage,
    TerminalAttachMessage,
    TerminalAttachedMessage,
    TerminalDetachMessage,
    TerminalFrameMessage,
    TerminalResyncMessage,
    UnsubscribeMessage,
)
from murder.app.service.gateway import ApplicationGateway
from murder.app.service.projection_registry import ProjectionProviderRegistry
from murder.facts.log import FactLog, ProjectionInputLog, ReplayGapError

TerminalCapture = Callable[[UUID], Awaitable[Any]]

def _aiohttp() -> Any:
    from aiohttp import WSMsgType, web
    return web, WSMsgType

@dataclass
class ApplicationConnection:
    websocket: Any
    client_id: str
    tasks: dict[str, asyncio.Task[None]] = field(default_factory=dict)
    terminals: dict[str, TerminalTarget] = field(default_factory=dict)

    async def send(self, message: object) -> None:
        await self.websocket.send_json(message.model_dump(mode="json"))

    async def cancel(self, key: str) -> None:
        task = self.tasks.pop(key, None)
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def close(self) -> None:
        for key in list(self.tasks):
            await self.cancel(key)
        await self.websocket.close()


class SubscriptionCoordinator:
    def __init__(self, facts: FactLog, projection_inputs: ProjectionInputLog, providers: ProjectionProviderRegistry) -> None:
        self._facts = facts
        self._inputs = projection_inputs
        self._providers = providers

    async def run(self, connection: ApplicationConnection, message: SubscribeMessage) -> None:
        spec = message.subscription
        if isinstance(spec, ProjectionSubscription):
            await self._projections(connection, message.subscription_id, spec)
        elif isinstance(spec, FactSubscription):
            await self._facts_stream(connection, message.subscription_id, spec)
        else:
            raise ValueError(f"unsupported application subscription {spec.kind}")

    async def _projections(self, connection: ApplicationConnection, subscription_id: str, spec: ProjectionSubscription) -> None:
        if not spec.topics:
            raise ValueError("projection subscriptions require at least one topic")
        topics = frozenset(topic.value for topic in spec.topics)
        for topic in spec.topics:
            if not self._providers.has_provider(topic):
                raise ValueError(f"projection {topic.value!r} has no feature provider")
        watermark = self._inputs.watermark()
        if spec.cursor is not None and self._inputs.is_cursor_retained(spec.cursor):
            replay = self._inputs.replay(after_sequence=spec.cursor, projections=topics, until_sequence=watermark)
            snapshots: dict[str, dict[str, object]] = {}
            mode = "resume"
        else:
            replay = ()
            snapshots = {topic.value: validate_snapshot(topic.value, self._providers.snapshot(topic)) for topic in spec.topics}
            mode = "cold" if spec.cursor is None else "snapshot_fallback"
        await connection.send(SubscriptionReadyMessage(subscription_id=subscription_id, snapshot=SubscriptionSnapshot(
            cursor=watermark, mode=mode, snapshots=snapshots,
            replay=[{"cursor": item.sequence, "payload": _input_payload(item)} for item in replay],
        )))
        cursor = watermark
        while True:
            try:
                async for item in self._inputs.tail(after_sequence=cursor, projections=topics):
                    cursor = item.sequence
                    await connection.send(SubscriptionEventMessage(subscription_id=subscription_id, cursor=cursor, payload=_input_payload(item)))
            except ReplayGapError:
                cursor = self._inputs.watermark()
                snapshots = {topic.value: validate_snapshot(topic.value, self._providers.snapshot(topic)) for topic in spec.topics}
                await connection.send(SubscriptionReadyMessage(subscription_id=subscription_id, snapshot=SubscriptionSnapshot(cursor=cursor, mode="snapshot_fallback", snapshots=snapshots)))

    async def _facts_stream(self, connection: ApplicationConnection, subscription_id: str, spec: FactSubscription) -> None:
        cursor = spec.cursor if spec.cursor is not None else self._facts.watermark()
        if not self._facts.is_cursor_retained(cursor):
            raise ValueError("fact cursor is outside retained fact history")
        watermark = self._facts.watermark()
        kinds = frozenset(spec.fact_kinds)
        replay = self._facts.replay(after_sequence=cursor, kinds=kinds, until_sequence=watermark)
        await connection.send(SubscriptionReadyMessage(subscription_id=subscription_id, snapshot=SubscriptionSnapshot(
            cursor=watermark, mode="resume" if spec.cursor is not None else "cold",
            replay=[{"cursor": item.sequence, "payload": item.model_dump(mode="json")} for item in replay],
        )))
        async for item in self._facts.tail(after_sequence=watermark, kinds=kinds):
            await connection.send(SubscriptionEventMessage(subscription_id=subscription_id, cursor=item.sequence, payload=item.model_dump(mode="json")))


class TerminalStreamCoordinator:
    def __init__(self, capture: TerminalCapture | None, *, interval_s: float = 0.1) -> None:
        self._capture = capture
        self._interval_s = interval_s
        self._sequences: dict[str, int] = {}

    async def run(self, connection: ApplicationConnection, message: TerminalAttachMessage) -> None:
        if self._capture is None:
            raise RuntimeError("terminal capture is unavailable")
        await connection.send(TerminalAttachedMessage(stream_id=message.stream_id))
        target = message.target
        key = str(target.session_id)
        sequence = max(message.after_sequence, self._sequences.get(key, 0))
        while True:
            captured = await self._capture(target.session_id)
            data = captured.data if hasattr(captured, "data") else str(captured)
            columns = getattr(captured, "columns", max(1, len(data)))
            rows = getattr(captured, "rows", max(1, len(data.splitlines())))
            sequence += 1
            self._sequences[key] = sequence
            await connection.send(TerminalFrameMessage(stream_id=message.stream_id, frame=TerminalFrame(
                subscription_id=message.stream_id, session_id=target.session_id,
                sequence=sequence, captured_at=datetime.now(timezone.utc), columns=columns, rows=rows, data=data,
            )))
            await asyncio.sleep(self._interval_s)


class ApplicationSocketServer:
    """WebSocket-only typed application server owned by the service process."""

    def __init__(
        self,
        *,
        gateway: ApplicationGateway,
        facts: FactLog,
        projection_inputs: ProjectionInputLog,
        providers: ProjectionProviderRegistry,
        run_id: str,
        terminal_capture: TerminalCapture | None = None,
        assets_dir: Path | None = None,
    ) -> None:
        self._gateway = gateway
        self._facts = facts
        self._inputs = projection_inputs
        self._run_id = run_id
        self._subscriptions = SubscriptionCoordinator(facts, projection_inputs, providers)
        self._terminals = TerminalStreamCoordinator(terminal_capture)
        self._assets_dir = assets_dir
        self._runner: Any = None
        self._site: Any = None
        self.bound: tuple[str, int] | None = None

    async def start(self, *, host: str = "127.0.0.1", port: int = 0) -> tuple[str, int]:
        web, _ = _aiohttp()
        app = web.Application()
        app.router.add_get("/api/ws", self._handle_websocket)
        if self._assets_dir is not None and self._assets_dir.is_dir():
            app.router.add_get("/{path:.*}", self._serve_asset)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, host, port)
        await self._site.start()
        server = next(iter(self._site._server.sockets))
        self.bound = (str(server.getsockname()[0]), int(server.getsockname()[1]))
        return self.bound

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
            self._site = None

    async def _handle_websocket(self, request: Any) -> Any:
        web, WSMsgType = _aiohttp()
        ws = web.WebSocketResponse(heartbeat=30.0)
        await ws.prepare(request)
        connection: ApplicationConnection | None = None
        try:
            first = await ws.receive()
            if first.type is not WSMsgType.TEXT:
                return ws
            hello = APPLICATION_WIRE_ADAPTER.validate_json(first.data)
            if not isinstance(hello, ClientHello):
                raise ValueError("first application message must be client.hello")
            if hello.protocol_version != APPLICATION_PROTOCOL_VERSION:
                raise ValueError("application protocol version mismatch")
            connection = ApplicationConnection(ws, hello.client.client_id)
            await connection.send(ServerHello(server_id=self._run_id, queries=list(self._gateway.available_queries), commands=list(self._gateway.available_commands), fact_cursor=self._facts.watermark(), projection_cursor=self._inputs.watermark()))
            async for raw in ws:
                if raw.type is not WSMsgType.TEXT:
                    continue
                await self._dispatch(connection, APPLICATION_WIRE_ADAPTER.validate_json(raw.data))
        except Exception as exc:
            if connection is not None:
                await connection.send(ErrorMessage(error={"code": ErrorCode.INVALID_MESSAGE, "message": str(exc)}))
        finally:
            if connection is not None:
                await connection.close()
        return ws

    async def _serve_asset(self, request: Any) -> Any:
        web, _ = _aiohttp()
        assert self._assets_dir is not None
        root = self._assets_dir.resolve()
        candidate = (root / request.match_info.get("path", "")).resolve()
        if not candidate.is_relative_to(root):
            raise web.HTTPForbidden()
        if candidate.is_file():
            return web.FileResponse(candidate)
        index = root / "index.html"
        if index.is_file():
            return web.FileResponse(index)
        raise web.HTTPNotFound()

    async def _dispatch(self, connection: ApplicationConnection, message: object) -> None:
        if isinstance(message, RequestMessage):
            try:
                result = await self._gateway.request(
                    message.request,
                    timeout_s=message.timeout_s,
                    authenticated_client_id=connection.client_id,
                    wire_request_id=message.request_id,
                )
                await connection.send(ReplyMessage(request_id=message.request_id, result=result))
            except Exception as exc:
                await connection.send(ErrorMessage(error={"code": ErrorCode.REQUEST_FAILED, "message": str(exc)}, request_id=message.request_id))
        elif isinstance(message, SubscribeMessage):
            await connection.cancel(message.subscription_id)
            connection.tasks[message.subscription_id] = asyncio.create_task(self._subscriptions.run(connection, message))
        elif isinstance(message, UnsubscribeMessage):
            await connection.cancel(message.subscription_id)
        elif isinstance(message, TerminalAttachMessage):
            await connection.cancel(message.stream_id)
            connection.terminals[message.stream_id] = message.target
            connection.tasks[message.stream_id] = asyncio.create_task(self._terminals.run(connection, message))
        elif isinstance(message, TerminalDetachMessage):
            connection.terminals.pop(message.stream_id, None)
            await connection.cancel(message.stream_id)
        elif isinstance(message, TerminalResyncMessage):
            target = connection.terminals.get(message.stream_id)
            if target is None:
                raise ValueError("terminal stream is not attached")
            await connection.cancel(message.stream_id)
            connection.tasks[message.stream_id] = asyncio.create_task(
                self._terminals.run(
                    connection,
                    TerminalAttachMessage(
                        stream_id=message.stream_id,
                        target=target,
                        after_sequence=message.after_sequence,
                    ),
                )
            )
        else:
            raise ValueError(f"client cannot send {getattr(message, 'op', 'unknown')}")


def _input_payload(item: object) -> dict[str, object]:
    payload = {"type": "projection.invalidate", "projection": item.projection, "subject_key": item.subject_key, "generation": item.generation, "source_fact_id": str(item.source_fact_id) if item.source_fact_id else None}
    return validate_event(item.projection, payload)


__all__ = ["ApplicationConnection", "ApplicationSocketServer", "SubscriptionCoordinator", "TerminalStreamCoordinator"]
