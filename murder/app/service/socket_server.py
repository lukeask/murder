"""Daemon HTTP + per-repo application WebSocket boundary.

``DaemonHttpServer`` owns the process listener (SPA, picker API,
``/api/ws/{repository_id}``). ``RepositorySocketSession`` owns one repo's
handshake/dispatch/terminal machinery. ``ApplicationSocketServer`` remains a
single-repo ``/api/ws`` compat wrapper for focused tests.

No Unix socket, bus envelope, generic publish, or RPC target is accepted here.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import logging
from collections.abc import Awaitable, Callable
from contextvars import Context
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
)
from murder.app.protocol.wire import (
    APPLICATION_WIRE_ADAPTER,
    ClientHello,
    ErrorMessage,
    PlanSeedFailedNotification,
    ReplyMessage,
    RequestMessage,
    ServerHello,
    SubscribeMessage,
    SubscriptionEventMessage,
    SubscriptionReadyMessage,
    TerminalAttachedMessage,
    TerminalAttachMessage,
    TerminalChunkMessage,
    TerminalDetachMessage,
    TerminalFrameMessage,
    TerminalInputAckMessage,
    TerminalInputDetachMessage,
    TerminalInputMessage,
    TerminalKeyframeMessage,
    TerminalResyncedMessage,
    TerminalResyncMessage,
    TerminalStreamGapMessage,
    UnsubscribeMessage,
)
from murder.app.service.gateway import ApplicationGateway
from murder.app.service.projection_registry import ProjectionProviderRegistry
from murder.facts.log import FactLog, ProjectionInputLog, ReplayGapError
from murder.observability.log_context import (
    adopt_observability_context,
    create_task_with_context,
)
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
    # Per-host observability Context (run_id / repository_id / advanced_log).
    task_context: Context | None = None

    async def send(self, message: object) -> None:
        await self.websocket.send_json(message.model_dump(mode="json"))

    async def cancel(self, key: str) -> None:
        task = self.tasks.pop(key, None)
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                LOGGER.warning("application task failed during cleanup key=%s", key, exc_info=True)

    async def close(self) -> None:
        for key in list(self.tasks):
            try:
                await self.cancel(key)
            except Exception:
                LOGGER.warning("application task cleanup failed key=%s", key, exc_info=True)
        with contextlib.suppress(Exception):
            await self.websocket.close()

    def start_stream(
        self,
        key: str,
        body: Callable[[], Awaitable[None]],
        *,
        error_stream_id: str | None = None,
        error_subscription_id: str | None = None,
        error_code: ErrorCode = ErrorCode.STREAM_FAILED,
        name: str | None = None,
    ) -> asyncio.Task[None]:
        async def run_stream() -> None:
            try:
                await body()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                LOGGER.warning("application stream failed key=%s", key, exc_info=True)
                with contextlib.suppress(Exception):
                    await self.send(
                        ErrorMessage(
                            stream_id=error_stream_id,
                            subscription_id=error_subscription_id,
                            error=ErrorBody(code=error_code, message=str(exc)),
                        )
                    )
            finally:
                if self.tasks.get(key) is asyncio.current_task():
                    self.tasks.pop(key, None)

        task = create_task_with_context(
            run_stream(),
            name=name,
            context=self.task_context,
        )
        self.tasks[key] = task
        return task


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
            connection.start_stream(
                _input_task_key(message.stream_id),
                lambda: self._run(connection, message.stream_id, state),
                error_stream_id=message.stream_id,
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
            raise RuntimeError("terminal input queue is full. Input is temporarily suspended.")
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
                # declare that absence of raw history. The keyframe below is
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


class RepositorySocketSession:
    """Per-repo WebSocket handshake / dispatch / terminal machinery.

    Constructed on demand from a live ``RepositoryHost``. Repo identity is not
    on the wire — callers route ``/api/ws/{repository_id}`` before handing off.
    ``server.hello.server_id`` is this host's ``run_id`` (restart detection).
    """

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
        task_context: Context | None = None,
    ) -> None:
        self._gateway = gateway
        self._facts = facts
        self._inputs = projection_inputs
        self._run_id = run_id
        self._task_context = task_context
        self._subscriptions = SubscriptionCoordinator(facts, projection_inputs, providers)
        self._terminals = TerminalStreamCoordinator(
            terminal_capture,
            terminal_output_open,
            interval_s=terminal_interval_s,
        )
        self._terminal_input = TerminalInputCoordinator(terminal_input, terminal_input_validator)
        self._connections: dict[str, ApplicationConnection] = {}
        self._closed = False

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def connection_count(self) -> int:
        return len(self._connections)

    @property
    def closed(self) -> bool:
        return self._closed

    async def close(self) -> None:
        """Close every active WebSocket and cancel their streams.

        New ``handle_websocket`` calls refuse after this; in-flight handlers exit
        via connection teardown.
        """
        self._closed = True
        connections = list(self._connections.values())
        self._connections.clear()
        if not connections:
            return
        await asyncio.gather(
            *(connection.close() for connection in connections),
            return_exceptions=True,
        )

    async def notify_plan_seed_failed(
        self, client_id: str | None, plan_name: str, message: str
    ) -> None:
        """Deliver an ephemeral planner-seed failure only to its initiating client."""
        if client_id is None:
            return
        connection = self._connections.get(client_id)
        if connection is None:
            return
        try:
            await connection.send(
                PlanSeedFailedNotification(plan_name=plan_name, message=message)
            )
        except Exception:
            LOGGER.debug("failed to deliver plan seed failure to %s", client_id, exc_info=True)

    async def handle_websocket(self, request: Any) -> Any:
        web, WSMsgType = _aiohttp()
        if self._closed:
            raise web.HTTPServiceUnavailable(text="repository session is closed")
        ws = web.WebSocketResponse(heartbeat=30.0)
        await ws.prepare(request)
        connection: ApplicationConnection | None = None
        # aiohttp runs this handler outside the host start task — adopt host
        # bindings so inline dispatch (and inherited child tasks) see the right
        # run_id / repository_id / advanced_log.
        if self._task_context is not None:
            adopt_observability_context(self._task_context)
        try:
            if self._closed:
                with contextlib.suppress(Exception):
                    await ws.close()
                return ws
            first = await ws.receive()
            if first.type is not WSMsgType.TEXT:
                return ws
            hello = APPLICATION_WIRE_ADAPTER.validate_json(first.data)
            if not isinstance(hello, ClientHello):
                raise ValueError("first application message must be client.hello")
            if hello.protocol_version != APPLICATION_PROTOCOL_VERSION:
                # Send before ApplicationConnection exists so stale clients can stop
                # reconnecting (Node treats version_mismatch as permanent).
                await ws.send_json(
                    ErrorMessage(
                        error={
                            "code": ErrorCode.VERSION_MISMATCH,
                            "message": (
                                "application protocol version mismatch: "
                                f"client={hello.protocol_version} "
                                f"server={APPLICATION_PROTOCOL_VERSION}"
                            ),
                        }
                    ).model_dump(mode="json")
                )
                return ws
            connection = ApplicationConnection(
                ws, hello.client.client_id, task_context=self._task_context
            )
            if self._closed:
                await connection.close()
                return ws
            self._connections[connection.client_id] = connection
            await connection.send(
                ServerHello(
                    server_id=self._run_id,
                    queries=list(self._gateway.available_queries),
                    commands=list(self._gateway.available_commands),
                    fact_cursor=self._facts.watermark(),
                    projection_cursor=self._inputs.watermark(),
                )
            )
            async for raw in ws:
                if raw.type is not WSMsgType.TEXT:
                    continue
                await self._dispatch(connection, APPLICATION_WIRE_ADAPTER.validate_json(raw.data))
        except Exception as exc:
            payload = ErrorMessage(
                error={"code": ErrorCode.INVALID_MESSAGE, "message": str(exc)}
            )
            if connection is not None:
                await connection.send(payload)
            else:
                with contextlib.suppress(Exception):
                    await ws.send_json(payload.model_dump(mode="json"))
        finally:
            if connection is not None:
                if self._connections.get(connection.client_id) is connection:
                    self._connections.pop(connection.client_id, None)
                await connection.close()
        return ws

    async def _dispatch(self, connection: ApplicationConnection, message: object) -> None:
        if isinstance(message, RequestMessage):
            key = f"request:{message.request_id}"
            await connection.cancel(key)
            connection.start_stream(
                key,
                lambda: self._run_request(connection, message),
                name=f"murder-request-{message.request_id}",
            )
            await asyncio.sleep(0)
        elif isinstance(message, SubscribeMessage):
            await connection.cancel(message.subscription_id)
            connection.start_stream(
                message.subscription_id,
                lambda: self._subscriptions.run(connection, message),
                error_subscription_id=message.subscription_id,
                error_code=ErrorCode.UNSUPPORTED_SUBSCRIPTION,
                name=f"murder-subscription-{message.subscription_id}",
            )
        elif isinstance(message, UnsubscribeMessage):
            await connection.cancel(message.subscription_id)
        elif isinstance(message, TerminalAttachMessage):
            await connection.cancel(message.stream_id)
            connection.terminals[message.stream_id] = message
            connection.start_stream(
                message.stream_id,
                lambda: self._terminals.run(connection, message),
                error_stream_id=message.stream_id,
                name=f"murder-terminal-{message.stream_id}",
            )
        elif isinstance(message, TerminalDetachMessage):
            connection.terminals.pop(message.stream_id, None)
            await connection.cancel(message.stream_id)
        elif isinstance(message, TerminalResyncMessage):
            attachment = connection.terminals.get(message.stream_id)
            if attachment is None:
                raise ValueError("terminal stream is not attached")
            await connection.cancel(message.stream_id)
            connection.start_stream(
                message.stream_id,
                lambda: self._terminals.run(
                    connection,
                    TerminalAttachMessage(
                        stream_id=message.stream_id,
                        target=attachment.target,
                        after_sequence=message.after_sequence,
                        mode=attachment.mode,
                    ),
                    resync=True,
                ),
                error_stream_id=message.stream_id,
                name=f"murder-terminal-{message.stream_id}",
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
        elif isinstance(message, TerminalInputDetachMessage):
            await self._terminal_input.detach(connection, message.stream_id)
        else:
            raise ValueError(f"client cannot send {getattr(message, 'op', 'unknown')}")

    async def _run_request(
        self, connection: ApplicationConnection, message: RequestMessage
    ) -> None:
        try:
            result = await self._gateway.request(
                message.request,
                timeout_s=message.timeout_s,
                authenticated_client_id=connection.client_id,
                wire_request_id=message.request_id,
            )
            await connection.send(ReplyMessage(request_id=message.request_id, result=result))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await connection.send(
                ErrorMessage(
                    error=ErrorBody(code=ErrorCode.REQUEST_FAILED, message=str(exc)),
                    request_id=message.request_id,
                )
            )


def session_from_host(repo_host: Any) -> RepositorySocketSession:
    """Build a ``RepositorySocketSession`` from a started ``RepositoryHost``."""
    assert repo_host.application_dispatcher is not None
    assert repo_host.fact_log is not None
    assert repo_host.projection_input_log is not None
    session = RepositorySocketSession(
        gateway=ApplicationGateway(
            repo_host.application_dispatcher,
            schedule_plan_seed=repo_host.schedule_plan_seed,
        ),
        facts=repo_host.fact_log,
        projection_inputs=repo_host.projection_input_log,
        providers=repo_host.projection_providers,
        run_id=repo_host.run_id,
        terminal_capture=repo_host.terminal_capture,
        terminal_output_open=repo_host.terminal_output_open,
        terminal_input=repo_host.terminal_input,
        terminal_input_validator=repo_host.terminal_input_validator,
        task_context=repo_host.observability_context,
    )
    repo_host.set_plan_seed_failure_notifier(session.notify_plan_seed_failed)
    return session


class DaemonHttpServer:
    """Process-wide aiohttp listener: SPA, picker API, path-scoped WS routing."""

    def __init__(
        self,
        *,
        manager: Any,
        assets_dir: Path | None = None,
    ) -> None:
        # ``manager`` is a ``RepositoryManager``; typed as Any to avoid import cycles.
        self._manager = manager
        self._assets_dir = assets_dir
        self._sessions: dict[str, RepositorySocketSession] = {}
        self._session_lock = asyncio.Lock()
        self._runner: Any = None
        self._site: Any = None
        self.bound: tuple[str, int] | None = None

    def session_for(self, repository_id: str) -> RepositorySocketSession | None:
        return self._sessions.get(repository_id)

    def drop_session(self, repository_id: str) -> None:
        """Forget a cached session without closing connections (sync helper)."""
        self._sessions.pop(repository_id, None)

    async def close_session(self, repository_id: str) -> None:
        """Prevent new attaches, close active WebSockets, and drop the cache.

        Wired as ``RepositoryManager.on_deactivated`` so deactivate always tears
        down transport before ``host.stop()``.
        """
        session = self._sessions.pop(repository_id, None)
        if session is None:
            return
        await session.close()

    async def ensure_session(self, repository_id: str) -> RepositorySocketSession:
        """Activate the host (if needed) and return a live socket session.

        Serialized so two concurrent first connects cannot orphan a session
        (split ``_connections`` / plan-seed notifier).
        """
        async with self._session_lock:
            host = await self._manager.activate_by_id(repository_id)
            existing = self._sessions.get(repository_id)
            if (
                existing is not None
                and existing.run_id == host.run_id
                and not existing.closed
            ):
                return existing
            session = session_from_host(host)
            self._sessions[repository_id] = session
            return session

    async def start(self, *, host: str = "127.0.0.1", port: int = 0) -> tuple[str, int]:
        web, _ = _aiohttp()
        app = web.Application()
        app.router.add_get("/api/repos", self._handle_list_repos)
        app.router.add_post("/api/repos/init", self._handle_init_repo)
        app.router.add_post("/api/repos/activate", self._handle_activate_repo)
        app.router.add_post("/api/repos/deactivate", self._handle_deactivate_repo)
        app.router.add_get("/api/ws/{repository_id}", self._handle_websocket)
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
        sessions = list(self._sessions.values())
        self._sessions.clear()
        if sessions:
            await asyncio.gather(
                *(session.close() for session in sessions),
                return_exceptions=True,
            )
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
            self._site = None

    async def _handle_list_repos(self, request: Any) -> Any:
        web, _ = _aiohttp()
        del request
        rows = [
            {
                "repository_id": entry.repository_id,
                "root_path": str(entry.root_path),
                "created_at": entry.created_at,
                "last_seen_at": entry.last_seen_at,
                "active": entry.repository_id in self._manager.active,
            }
            for entry in self._manager.list_recent()
        ]
        return web.json_response({"repositories": rows})

    async def _handle_init_repo(self, request: Any) -> Any:
        web, _ = _aiohttp()
        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text="JSON body required") from None
        if not isinstance(body, dict):
            raise web.HTTPBadRequest(text="JSON object required")
        raw_path = body.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise web.HTTPBadRequest(text="'path' string is required")
        force = bool(body.get("force", False))
        from murder.app.service.project_scaffold import ProjectAlreadyInitialized

        try:
            entry = self._manager.initialize(Path(raw_path).expanduser(), force=force)
        except ProjectAlreadyInitialized as exc:
            raise web.HTTPConflict(text=str(exc)) from exc
        except (OSError, ValueError, RuntimeError) as exc:
            raise web.HTTPBadRequest(text=str(exc)) from exc
        return web.json_response(
            {
                "repository_id": entry.repository_id,
                "root_path": str(entry.root_path),
                "created_at": entry.created_at,
                "last_seen_at": entry.last_seen_at,
            },
            status=201,
        )

    def _repo_websocket_url(self, repository_id: str) -> str:
        host, port = self.bound or ("127.0.0.1", 0)
        return f"ws://{host}:{port}/api/ws/{repository_id}"

    async def _handle_activate_repo(self, request: Any) -> Any:
        """Activate (or reuse) a host for ``path`` and warm its socket session."""
        web, _ = _aiohttp()
        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text="JSON body required") from None
        if not isinstance(body, dict):
            raise web.HTTPBadRequest(text="JSON object required")
        raw_path = body.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise web.HTTPBadRequest(text="'path' string is required")
        root = Path(raw_path).expanduser().resolve(strict=False)
        try:
            host = await self._manager.activate(root)
            await self.ensure_session(host.repository_id)
        except FileNotFoundError as exc:
            raise web.HTTPNotFound(text=str(exc)) from exc
        except KeyError as exc:
            raise web.HTTPNotFound(text=str(exc)) from exc
        except (OSError, ValueError, RuntimeError) as exc:
            raise web.HTTPBadRequest(text=str(exc)) from exc
        except Exception as exc:
            LOGGER.exception("failed to activate path=%s", root)
            raise web.HTTPInternalServerError(text=str(exc)) from exc
        return web.json_response(
            {
                "repository_id": host.repository_id,
                "root_path": str(host.repo_root),
                "websocket_url": self._repo_websocket_url(host.repository_id),
                "active": True,
            }
        )

    async def _handle_deactivate_repo(self, request: Any) -> Any:
        """Deactivate one host by ``path`` or ``repository_id`` (daemon stays up)."""
        web, _ = _aiohttp()
        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text="JSON body required") from None
        if not isinstance(body, dict):
            raise web.HTTPBadRequest(text="JSON object required")
        repository_id = body.get("repository_id")
        raw_path = body.get("path")
        if isinstance(repository_id, str) and repository_id.strip():
            rid = repository_id.strip()
        elif isinstance(raw_path, str) and raw_path.strip():
            root = Path(raw_path).expanduser().resolve(strict=False)
            host = self._manager.get_by_root(root)
            if host is None:
                # Not active — treat as success (idempotent stop).
                return web.json_response({"ok": True, "active": False})
            rid = host.repository_id
        else:
            raise web.HTTPBadRequest(text="'path' or 'repository_id' is required")
        if self._manager.get(rid) is None:
            await self.close_session(rid)
            return web.json_response({"ok": True, "active": False})
        await self._manager.deactivate(rid)
        # Belt-and-suspenders: manager.on_deactivated usually closes this, but the
        # HTTP path owns the session cache and must not leave a stale entry if the
        # callback was never wired (tests) or failed.
        await self.close_session(rid)
        return web.json_response({"ok": True, "repository_id": rid, "active": False})

    async def _handle_websocket(self, request: Any) -> Any:
        web, _ = _aiohttp()
        repository_id = request.match_info["repository_id"]
        if self._manager.resolve_root(repository_id) is None and self._manager.get(
            repository_id
        ) is None:
            raise web.HTTPNotFound(text=f"unknown repository_id: {repository_id}")
        try:
            session = await self.ensure_session(repository_id)
        except KeyError as exc:
            raise web.HTTPNotFound(text=str(exc)) from exc
        except Exception as exc:
            from murder.app.service.repository_manager import StaleRepositoryError

            if isinstance(exc, StaleRepositoryError):
                raise web.HTTPGone(text=str(exc)) from exc
            if isinstance(exc, FileNotFoundError):
                raise web.HTTPNotFound(text=str(exc)) from exc
            LOGGER.exception("failed to activate repository_id=%s", repository_id)
            raise web.HTTPInternalServerError(text=str(exc)) from exc
        self._manager.note_ws_connect(repository_id)
        try:
            return await session.handle_websocket(request)
        finally:
            self._manager.note_ws_disconnect(repository_id)

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


class ApplicationSocketServer:
    """Single-repo test / compat listener binding ``RepositorySocketSession`` at ``/api/ws``.

    Production daemons use ``DaemonHttpServer`` with ``/api/ws/{repository_id}``.
    """

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
        self._session = RepositorySocketSession(
            gateway=gateway,
            facts=facts,
            projection_inputs=projection_inputs,
            providers=providers,
            run_id=run_id,
            terminal_capture=terminal_capture,
            terminal_output_open=terminal_output_open,
            terminal_input=terminal_input,
            terminal_input_validator=terminal_input_validator,
            terminal_interval_s=terminal_interval_s,
        )
        self._assets_dir = assets_dir
        self._runner: Any = None
        self._site: Any = None
        self.bound: tuple[str, int] | None = None

    async def notify_plan_seed_failed(
        self, client_id: str | None, plan_name: str, message: str
    ) -> None:
        await self._session.notify_plan_seed_failed(client_id, plan_name, message)

    async def start(self, *, host: str = "127.0.0.1", port: int = 0) -> tuple[str, int]:
        web, _ = _aiohttp()
        app = web.Application()
        app.router.add_get("/api/ws", self._session.handle_websocket)
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


__all__ = [
    "ApplicationConnection",
    "ApplicationSocketServer",
    "DaemonHttpServer",
    "RepositorySocketSession",
    "SubscriptionCoordinator",
    "TerminalInputCoordinator",
    "TerminalStreamCoordinator",
    "session_from_host",
]
