"""The service's single application-only WebSocket boundary.

No Unix socket, bus envelope, generic publish, or RPC target is accepted here.
The connection class owns one peer, while the two coordinators own the only
long-running application streams.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, cast
from uuid import UUID

from murder.app.protocol.common import APPLICATION_PROTOCOL_VERSION, ErrorBody, ErrorCode
from murder.app.protocol.projections import validate_event, validate_snapshot
from murder.app.protocol.subscriptions import (
    FactSubscription,
    ProjectionSubscription,
    SubscriptionSnapshot,
)
from murder.app.protocol.terminal import (
    TerminalBuffer,
    TerminalCell,
    TerminalChunk,
    TerminalColor,
    TerminalCursor,
    TerminalFrame,
    TerminalKeyframe,
    TerminalModes,
    TerminalRendition,
    TerminalStreamGap,
    TerminalTarget,
)
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
    TerminalChunkMessage,
    TerminalFrameMessage,
    TerminalKeyframeMessage,
    TerminalInputAckMessage,
    TerminalInputMessage,
    TerminalResyncMessage,
    TerminalResyncedMessage,
    TerminalStreamGapMessage,
    UnsubscribeMessage,
)
from murder.app.service.gateway import ApplicationGateway
from murder.app.service.projection_registry import ProjectionProviderRegistry
from murder.facts.log import FactLog, ProjectionInputLog, ReplayGapError
from murder.runtime.terminal.output import TmuxTerminalOutput
from murder.runtime.terminal.vt import VtBufferSnapshot, VtCell, VtRendition

TerminalCapture = Callable[[UUID], Awaitable[Any]]
TerminalOutputOpen = Callable[[UUID], Awaitable[TmuxTerminalOutput]]
TerminalInput = Callable[[UUID, str, UUID, int, bytes], Awaitable[None]]
TerminalInputValidator = Callable[[UUID, str, UUID, int], Awaitable[None]]

LOGGER = logging.getLogger(__name__)
_MAX_TERMINAL_INPUT_BYTES = 256 * 1024
_MAX_TERMINAL_INPUT_QUEUE_ITEMS = 128
_MAX_TERMINAL_INPUT_QUEUE_BYTES = 512 * 1024

def _aiohttp() -> Any:
    from aiohttp import WSMsgType, web
    return web, WSMsgType

@dataclass
class ApplicationConnection:
    websocket: Any
    client_id: str
    tasks: dict[str, asyncio.Task[None]] = field(default_factory=dict)
    terminals: dict[str, TerminalAttachMessage] = field(default_factory=dict)
    input_streams: dict[str, _TerminalInputState] = field(default_factory=dict)

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


@dataclass
class _TerminalInputState:
    """Per-connection serial input queue for one attached terminal stream."""

    session_id: UUID
    next_sequence: int = 1
    queued_bytes: int = 0
    queue: asyncio.Queue[tuple[int, UUID, int, bytes] | None] = field(
        default_factory=lambda: asyncio.Queue(maxsize=_MAX_TERMINAL_INPUT_QUEUE_ITEMS)
    )


class TerminalInputCoordinator:
    """Validate, fence, and serialize raw terminal input batches.

    WebSocket order is preserved by sequence validation at enqueue and by one
    writer coroutine per stream.  The runtime callback is expected to make the
    final fenced lease check at the controller mailbox boundary immediately
    before tmux I/O.
    """

    def __init__(
        self,
        writer: TerminalInput | None,
        validator: TerminalInputValidator | None,
    ) -> None:
        self._writer = writer
        self._validator = validator

    async def accept(
        self,
        connection: ApplicationConnection,
        message: TerminalInputMessage,
    ) -> None:
        if self._writer is None:
            raise RuntimeError("terminal input is unavailable")
        if self._validator is None:
            raise RuntimeError("terminal input validation is unavailable")
        try:
            data = base64.b64decode(message.data, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("terminal input contains invalid base64") from exc
        if not data:
            raise ValueError("terminal input must not be empty")
        if len(data) > _MAX_TERMINAL_INPUT_BYTES:
            raise ValueError("terminal input batch exceeds byte limit")
        # Reject a stale owner before it is ever acknowledged or consumes
        # queue capacity.  The writer repeats this at the controller mailbox
        # boundary because a lease may expire/take over while queued.
        await self._validator(
            message.session_id,
            connection.client_id,
            message.lease_id,
            message.fence,
        )

        state = connection.input_streams.get(message.stream_id)
        if state is None:
            state = _TerminalInputState(session_id=message.session_id)
            connection.input_streams[message.stream_id] = state
            connection.tasks[_input_task_key(message.stream_id)] = asyncio.create_task(
                self._run(connection, message.stream_id, state),
                name=f"murder-terminal-input-{message.stream_id}",
            )
        if state.session_id != message.session_id:
            raise ValueError("terminal input stream changed target")
        if message.input_sequence < state.next_sequence:
            LOGGER.info(
                "terminal input duplicate rejected stream=%s sequence=%d expected=%d",
                message.stream_id,
                message.input_sequence,
                state.next_sequence,
            )
            await connection.send(
                TerminalInputAckMessage(
                    stream_id=message.stream_id,
                    accepted_through=state.next_sequence - 1,
                )
            )
            return
        if message.input_sequence > state.next_sequence:
            LOGGER.warning(
                "terminal input sequence gap stream=%s sequence=%d expected=%d",
                message.stream_id,
                message.input_sequence,
                state.next_sequence,
            )
            raise ValueError(
                "terminal input sequence gap: "
                f"expected {state.next_sequence}, got {message.input_sequence}"
            )
        if state.queued_bytes + len(data) > _MAX_TERMINAL_INPUT_QUEUE_BYTES or state.queue.full():
            LOGGER.warning(
                "terminal input queue overflow stream=%s queued_bytes=%d incoming_bytes=%d",
                message.stream_id,
                state.queued_bytes,
                len(data),
            )
            raise RuntimeError("terminal input queue is full; input is temporarily suspended")
        state.queue.put_nowait((message.input_sequence, message.lease_id, message.fence, data))
        state.queued_bytes += len(data)
        state.next_sequence += 1
        LOGGER.debug(
            "terminal input batch accepted stream=%s sequence=%d bytes=%d",
            message.stream_id,
            message.input_sequence,
            len(data),
        )
        await connection.send(
            TerminalInputAckMessage(
                stream_id=message.stream_id,
                accepted_through=message.input_sequence,
            )
        )

    async def detach(self, connection: ApplicationConnection, stream_id: str) -> None:
        connection.input_streams.pop(stream_id, None)
        await connection.cancel(_input_task_key(stream_id))

    async def _run(
        self,
        connection: ApplicationConnection,
        stream_id: str,
        state: _TerminalInputState,
    ) -> None:
        assert self._writer is not None
        while True:
            item = await state.queue.get()
            if item is None:
                return
            sequence, lease_id, fence, data = item
            started = asyncio.get_running_loop().time()
            try:
                await self._writer(state.session_id, connection.client_id, lease_id, fence, data)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                LOGGER.warning(
                    "terminal input write failed stream=%s sequence=%d bytes=%d error=%s",
                    stream_id,
                    sequence,
                    len(data),
                    exc,
                )
                await connection.send(
                    ErrorMessage(
                        stream_id=stream_id,
                        error=ErrorBody(code=ErrorCode.STREAM_FAILED, message=str(exc)),
                    )
                )
            finally:
                state.queued_bytes -= len(data)
                LOGGER.debug(
                    "terminal input write complete stream=%s sequence=%d bytes=%d latency_ms=%.1f",
                    stream_id,
                    sequence,
                    len(data),
                    (asyncio.get_running_loop().time() - started) * 1000,
                )


def _input_task_key(stream_id: str) -> str:
    return f"terminal-input:{stream_id}"


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
    def __init__(
        self,
        capture: TerminalCapture | None,
        output_open: TerminalOutputOpen | None = None,
        *,
        interval_s: float = 0.1,
    ) -> None:
        self._capture = capture
        self._output_open = output_open
        self._interval_s = interval_s
        self._sequences: dict[str, int] = {}

    async def run(
        self,
        connection: ApplicationConnection,
        message: TerminalAttachMessage,
        *,
        resync: bool = False,
    ) -> None:
        if self._capture is None and self._output_open is None:
            raise RuntimeError("terminal capture is unavailable")
        if message.mode == "raw" and self._output_open is not None:
            await self._run_raw(connection, message, resync=resync)
            return
        await self._run_legacy_capture(connection, message)

    async def _run_legacy_capture(
        self, connection: ApplicationConnection, message: TerminalAttachMessage
    ) -> None:
        """Compatibility path for callers that do not provide native output."""
        if self._capture is None:
            raise RuntimeError("terminal capture is unavailable")
        await connection.send(TerminalAttachedMessage(stream_id=message.stream_id, mode="replace"))
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

    async def _run_raw(
        self,
        connection: ApplicationConnection,
        message: TerminalAttachMessage,
        *,
        resync: bool,
    ) -> None:
        assert self._output_open is not None
        output = await self._output_open(message.target.session_id)
        if not resync:
            await connection.send(TerminalAttachedMessage(stream_id=message.stream_id, mode="raw"))
        keyframe = await output.keyframe()
        if keyframe.snapshot is None:
            raise RuntimeError("terminal output did not produce a keyframe")
        if resync:
            await connection.send(
                TerminalResyncedMessage(
                    stream_id=message.stream_id,
                    keyframe=_terminal_keyframe(keyframe),
                )
            )
        else:
            if output.bootstrap_may_have_gap:
                # The initial capture-pane bootstrap and read-only control
                # attach have no common tmux ordering barrier. Explicitly
                # declare that absence of raw history; the keyframe below is
                # authoritative recovery state.
                await connection.send(
                    TerminalStreamGapMessage(
                        stream_id=message.stream_id,
                        gap=TerminalStreamGap(
                            expected_sequence=1,
                            next_sequence=keyframe.sequence,
                        ),
                    )
                )
            await connection.send(
                TerminalKeyframeMessage(
                    stream_id=message.stream_id,
                    keyframe=_terminal_keyframe(keyframe),
                )
            )
        sequence = keyframe.sequence
        while True:
            updates = await output.updates_after(sequence)
            if not updates:
                update = await output.wait_for_update(sequence)
                if update is None:
                    return
                updates = (update,)
            for update in updates:
                if update.sequence != sequence + 1:
                    await connection.send(
                        TerminalStreamGapMessage(
                            stream_id=message.stream_id,
                            gap=TerminalStreamGap(
                                expected_sequence=sequence + 1,
                                next_sequence=update.sequence,
                            ),
                        )
                    )
                    replacement = await output.keyframe()
                    if replacement.snapshot is None:
                        raise RuntimeError("terminal output did not produce a replacement keyframe")
                    await connection.send(
                        TerminalKeyframeMessage(
                            stream_id=message.stream_id,
                            keyframe=_terminal_keyframe(replacement),
                        )
                    )
                    sequence = replacement.sequence
                    break
                if update.kind == "chunk":
                    assert update.data is not None
                    await connection.send(
                        TerminalChunkMessage(
                            stream_id=message.stream_id,
                            chunk=TerminalChunk(
                                sequence=update.sequence,
                                encoding="base64",
                                data=base64.b64encode(update.data).decode("ascii"),
                            ),
                        )
                    )
                else:
                    assert update.snapshot is not None
                    await connection.send(
                        TerminalKeyframeMessage(
                            stream_id=message.stream_id,
                            keyframe=_terminal_keyframe(update),
                        )
                    )
                sequence = update.sequence


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
        terminal_output_open: TerminalOutputOpen | None = None,
        terminal_input: TerminalInput | None = None,
        terminal_input_validator: TerminalInputValidator | None = None,
        terminal_interval_s: float = 0.1,
        assets_dir: Path | None = None,
    ) -> None:
        self._gateway = gateway
        self._facts = facts
        self._inputs = projection_inputs
        self._run_id = run_id
        self._subscriptions = SubscriptionCoordinator(facts, projection_inputs, providers)
        self._terminals = TerminalStreamCoordinator(
            terminal_capture,
            terminal_output_open,
            interval_s=terminal_interval_s,
        )
        self._terminal_input = TerminalInputCoordinator(terminal_input, terminal_input_validator)
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
            connection.terminals[message.stream_id] = message
            connection.tasks[message.stream_id] = asyncio.create_task(self._terminals.run(connection, message))
        elif isinstance(message, TerminalDetachMessage):
            connection.terminals.pop(message.stream_id, None)
            await connection.cancel(message.stream_id)
        elif isinstance(message, TerminalResyncMessage):
            attachment = connection.terminals.get(message.stream_id)
            if attachment is None:
                raise ValueError("terminal stream is not attached")
            await connection.cancel(message.stream_id)
            connection.tasks[message.stream_id] = asyncio.create_task(
                self._terminals.run(
                    connection,
                    TerminalAttachMessage(
                        stream_id=message.stream_id,
                        target=attachment.target,
                        after_sequence=message.after_sequence,
                        mode=attachment.mode,
                    ),
                    resync=True,
                )
            )
        elif isinstance(message, TerminalInputMessage):
            try:
                await self._terminal_input.accept(connection, message)
            except Exception as exc:
                # A bad/stale batch is scoped to its input stream, not a
                # protocol-fatal condition for unrelated subscriptions.
                await connection.send(
                    ErrorMessage(
                        stream_id=message.stream_id,
                        error=ErrorBody(code=ErrorCode.STREAM_FAILED, message=str(exc)),
                    )
                )
        else:
            raise ValueError(f"client cannot send {getattr(message, 'op', 'unknown')}")


def _input_payload(item: object) -> dict[str, object]:
    payload = {"type": "projection.invalidate", "projection": item.projection, "subject_key": item.subject_key, "generation": item.generation, "source_fact_id": str(item.source_fact_id) if item.source_fact_id else None}
    return validate_event(item.projection, payload)


def _terminal_keyframe(update: Any) -> TerminalKeyframe:
    """Lower the backend's renderer-neutral VT state into the wire contract."""

    snapshot = update.snapshot
    assert snapshot is not None
    active = snapshot.alternate if snapshot.active_buffer == "alternate" else snapshot.primary
    return TerminalKeyframe(
        sequence=update.sequence,
        captured_at=update.captured_at,
        columns=snapshot.columns,
        rows=snapshot.rows,
        primary=_terminal_buffer(snapshot.primary),
        alternate=_terminal_buffer(snapshot.alternate),
        active_buffer=snapshot.active_buffer,
        rendition=_terminal_rendition(active.rendition),
        modes=TerminalModes(
            application_cursor=snapshot.modes.application_cursor,
            application_keypad=snapshot.modes.application_keypad,
            bracketed_paste=snapshot.modes.bracketed_paste,
            insert=snapshot.modes.insert,
            origin=snapshot.modes.origin,
            wraparound=snapshot.modes.wraparound,
            synchronized_updates=snapshot.modes.synchronized_updates,
        ),
    )


def _terminal_buffer(buffer: VtBufferSnapshot) -> TerminalBuffer:
    return TerminalBuffer(
        cells=[_terminal_cell(cell) for row in buffer.cells for cell in row],
        cursor=_terminal_cursor(buffer.cursor),
        saved_cursor=_terminal_cursor(buffer.saved_cursor),
        rendition=_terminal_rendition(buffer.rendition),
        saved_rendition=_terminal_rendition(buffer.saved_rendition),
        scroll_top=buffer.scroll_top,
        scroll_bottom=buffer.scroll_bottom,
        wrap_pending=buffer.wrap_pending,
    )


def _terminal_cell(cell: VtCell) -> TerminalCell:
    width = cast(Literal[0, 1, 2], cell.width)
    return TerminalCell(text=cell.text, width=width, rendition=_terminal_rendition(cell.rendition))


def _terminal_cursor(cursor: Any) -> TerminalCursor:
    return TerminalCursor(
        column=cursor.x,
        row=cursor.y,
        visible=cursor.visible,
        shape=cursor.shape,
    )


def _terminal_rendition(rendition: VtRendition) -> TerminalRendition:
    return TerminalRendition(
        foreground=_terminal_color(rendition.foreground),
        background=_terminal_color(rendition.background),
        bold=rendition.bold,
        faint=rendition.faint,
        italic=rendition.italic,
        underline=rendition.underline,
        blink=rendition.blink,
        inverse=rendition.inverse,
        invisible=rendition.invisible,
        strikethrough=rendition.strike,
    )


def _terminal_color(color: int | tuple[int, int, int] | None) -> TerminalColor:
    if color is None:
        return TerminalColor(kind="default")
    if isinstance(color, int):
        return TerminalColor(kind="indexed", index=color)
    return TerminalColor(kind="rgb", red=color[0], green=color[1], blue=color[2])


__all__ = ["ApplicationConnection", "ApplicationSocketServer", "SubscriptionCoordinator", "TerminalInputCoordinator", "TerminalStreamCoordinator"]
