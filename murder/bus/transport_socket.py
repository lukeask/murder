from __future__ import annotations

import asyncio
import contextlib
import errno
import ipaddress
import json
import logging
import re
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from murder.app.protocol.common import (
    APPLICATION_PROTOCOL_VERSION,
)
from murder.app.protocol.common import (
    ClientKind as ApplicationClientKind,
)
from murder.app.protocol.common import (
    ErrorBody as ApplicationErrorBody,
)
from murder.app.protocol.common import (
    ErrorCode as ApplicationErrorCode,
)
from murder.app.protocol.subscriptions import (
    NotificationChannel,
    NotificationSubscription,
    ProjectionSubscription,
    SubscriptionSnapshot,
)
from murder.app.protocol.terminal import TerminalFrame, TerminalTarget
from murder.app.protocol.wire import (
    APPLICATION_WIRE_ADAPTER,
    SubscriptionEventMessage,
    SubscriptionReadyMessage,
    TerminalAttachedMessage,
    TerminalAttachMessage,
    TerminalDetachMessage,
    TerminalFrameMessage,
    TerminalResyncedMessage,
    TerminalResyncMessage,
    UnsubscribeMessage,
)
from murder.app.protocol.wire import (
    ClientHello as ApplicationClientHello,
)
from murder.app.protocol.wire import (
    ErrorMessage as ApplicationErrorMessage,
)
from murder.app.protocol.wire import (
    ReplyMessage as ApplicationReplyMessage,
)
from murder.app.protocol.wire import (
    RequestMessage as ApplicationRequestMessage,
)
from murder.app.protocol.wire import (
    ServerHello as ApplicationServerHello,
)
from murder.app.protocol.wire import (
    SubscribeMessage as ApplicationSubscribeMessage,
)
from murder.app.service.gateway import ApplicationGateway
from murder.bus.broker import DurableBroker
from murder.bus.protocol import (
    PRESENCE_DISCONNECT_DEBOUNCE_S,
    PRESENCE_USER_KINDS,
    PROTOCOL_VERSION,
    WIRE_MESSAGE_ADAPTER,
    AckBody,
    AckMessage,
    ClientKind,
    Entity,
    ErrBody,
    ErrMessage,
    EventFilter,
    HelloMessage,
    HydrateMessage,
    HydrateReplayEvent,
    HydrateReply,
    PresenceEvent,
    PresenceState,
    PubMessage,
    RpcMessage,
    SubMessage,
    TmuxFrameEvent,
    WakeBody,
    WakeMessage,
)
from murder.state.storage.service_registry import socket_path_for_repo


@dataclass(frozen=True, slots=True)
class CapturedTerminalFrame:
    """One adapter capture paired with the pane geometry that produced it."""

    data: str
    columns: int
    rows: int


# The argument is a persisted session UUID string, an explicit legacy agent id,
# or None for the supervisor compatibility target.  Production returns exact
# tmux geometry; a string return remains accepted for lightweight test doubles.
TmuxFrameCapture = Callable[
    [str | None],
    Awaitable[CapturedTerminalFrame | str],
]

# Default interval between frame captures (seconds).  Chosen to be responsive
# without hammering tmux; Ink renders at terminal frame-rate so 100ms is plenty.
TMUX_FRAME_INTERVAL_S = 0.1
_ANSI_ESCAPE_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|[@-_])")


LOGGER = logging.getLogger(__name__)

_ACCEPT_RESOURCE_ERRNOS = frozenset({errno.EMFILE, errno.ENFILE, errno.ENOBUFS, errno.ENOMEM})
_ACCEPT_BACKOFF_MIN = 0.1
_ACCEPT_BACKOFF_MAX = 30.0

_HYDRATE_SNAPSHOT_TARGETS: dict[str, str] = {
    "conversations": "state.conversations_snapshot",
    "crow": "state.crow_snapshot",
    "schedule": "state.schedule_snapshot",
    "favorites": "tui.load_favorites",
    "templates": "tui.load_templates",
    "themes": "tui.load_themes",
    "workflows": "tui.load_workflows",
    "settings": "settings.get",
}
_HYDRATE_ALL_TOPICS = tuple(_HYDRATE_SNAPSHOT_TARGETS)


def _terminal_frame_geometry(frame: str) -> tuple[int, int]:
    """Return a conservative rendered geometry for a captured tmux snapshot."""

    lines = frame.splitlines() or [""]
    visible = [_ANSI_ESCAPE_RE.sub("", line) for line in lines]
    return max(1, *(len(line) for line in visible)), max(1, len(lines))


@dataclass
class _ClientSession:
    client_id: str
    kind: ClientKind
    transport: UdsTransport
    subscriptions: set[asyncio.Task[None]] = field(default_factory=set)
    application_tasks: dict[str, asyncio.Task[None]] = field(default_factory=dict)
    application_terminals: dict[str, TerminalTarget] = field(default_factory=dict)
    application: bool = False


class SocketBusServer:
    def __init__(
        self,
        broker: DurableBroker,
        *,
        run_id: str,
        socket_path: Path | None = None,
        disconnect_debounce_s: float = PRESENCE_DISCONNECT_DEBOUNCE_S,
        tmux_frame_capture: TmuxFrameCapture | None = None,
        tmux_frame_interval_s: float = TMUX_FRAME_INTERVAL_S,
    ) -> None:
        self._broker = broker
        self._run_id = run_id
        self._socket_path = socket_path or default_socket_path()
        self._disconnect_debounce_s = disconnect_debounce_s
        self._tmux_frame_capture = tmux_frame_capture
        self._tmux_frame_interval_s = tmux_frame_interval_s
        # Terminal ordering belongs to the target, not a connection or stream
        # id. Counters survive detach/reattach and are seeded by reconnecting
        # clients so a restarted service can continue above their last frame.
        self._terminal_sequences: dict[tuple[str, str | None], int] = {}
        self._terminal_capture_locks: dict[
            tuple[str, str | None],
            asyncio.Lock,
        ] = {}
        self._server: asyncio.AbstractServer | None = None
        self._tcp_server: asyncio.AbstractServer | None = None
        self._clients: dict[int, _ClientSession] = {}
        self._presence_state = PresenceState.HEADLESS
        self._presence_version = 0
        self._presence_task: asyncio.Task[None] | None = None
        self._kind_counts: dict[ClientKind, int] = {}
        self._closed = False
        self._accept_backoff_delay: float = 0.0
        self._accept_backoff_task: asyncio.Task[None] | None = None
        self._prior_exception_handler: Any = None
        self._installed_exception_handler = False
        self._application_gateway = ApplicationGateway(broker)

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
        self._install_accept_backoff_handler()

    async def start_tcp_listener(self, host: str = "127.0.0.1", port: int = 0) -> tuple[str, int]:
        """Start an additional TCP listener; returns (bound_host, bound_port).

        Uses the same _handle_client path as the Unix socket — the protocol is
        identical, so any bus client that speaks the wire protocol can connect
        over TCP (useful for web adapters / remote tooling).

        SECURITY (v0): this exposes the full wire protocol — publish, RPC, and
        subscribe-to-everything (the entire audit log) — over plain TCP with
        NO TLS and NO authentication. ``HelloBody.client_id`` is self-asserted,
        so anyone who can reach the port can forge events and drive RPCs. We
        therefore refuse non-loopback binds: exposing this beyond localhost
        needs an auth/TLS story first.
        """
        if not _is_loopback_host(host):
            raise ValueError(
                f"refusing to bind unauthenticated TCP bus listener to non-loopback host {host!r}; "
                "the wire protocol has no auth/TLS (v0)"
            )
        self._tcp_server = await asyncio.start_server(self._handle_client, host, port)
        sockets = self._tcp_server.sockets
        if not sockets:
            raise RuntimeError("TCP server started without bound sockets")
        bound = sockets[0].getsockname()
        return str(bound[0]), int(bound[1])

    async def stop(self) -> None:
        self._closed = True
        if self._accept_backoff_task is not None:
            self._accept_backoff_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._accept_backoff_task
            self._accept_backoff_task = None
        # Only restore the loop handler if start() actually installed ours;
        # otherwise we'd wipe a handler the host app (or another server) owns.
        if self._installed_exception_handler:
            loop = asyncio.get_event_loop()
            loop.set_exception_handler(self._prior_exception_handler)
            self._installed_exception_handler = False
        if self._presence_task is not None:
            self._presence_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._presence_task
        for session in list(self._clients.values()):
            for task in list(session.subscriptions):
                task.cancel()
            for task in list(session.application_tasks.values()):
                task.cancel()
            await session.transport.close()
        self._clients.clear()
        if self._tcp_server is not None:
            self._tcp_server.close()
            await self._tcp_server.wait_closed()
            self._tcp_server = None
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        if self._socket_path.exists():
            self._socket_path.unlink()

    def _install_accept_backoff_handler(self) -> None:
        loop = asyncio.get_event_loop()
        self._prior_exception_handler = loop.get_exception_handler()
        self._installed_exception_handler = True

        def _handler(loop: asyncio.AbstractEventLoop, ctx: dict[str, Any]) -> None:
            exc = ctx.get("exception")
            if isinstance(exc, OSError) and exc.errno in _ACCEPT_RESOURCE_ERRNOS:
                self._on_accept_resource_error(exc)
                return
            if self._prior_exception_handler is not None:
                self._prior_exception_handler(loop, ctx)
            else:
                loop.default_exception_handler(ctx)

        loop.set_exception_handler(_handler)

    def _on_accept_resource_error(self, exc: OSError) -> None:
        if self._accept_backoff_task is not None:
            return  # already backing off, nothing to do
        delay = min(
            max(_ACCEPT_BACKOFF_MIN, self._accept_backoff_delay * 2),
            _ACCEPT_BACKOFF_MAX,
        )
        self._accept_backoff_delay = delay
        LOGGER.warning("socket accept failed (errno %d), pausing for %.1fs", exc.errno, delay)
        servers = [s for s in (self._server, self._tcp_server) if s is not None]
        for srv in servers:
            srv.pause_serving()
        self._accept_backoff_task = asyncio.create_task(
            self._resume_after_backoff(delay, servers),
            name="accept-backoff",
        )

    async def _resume_after_backoff(
        self, delay: float, servers: list[asyncio.AbstractServer]
    ) -> None:
        await asyncio.sleep(delay)
        self._accept_backoff_task = None
        if self._closed:
            return
        for srv in servers:
            srv.resume_serving()
        LOGGER.info("socket accept resumed after %.1fs backoff", delay)

    async def _handle_client(  # noqa: PLR0912,PLR0915
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        self._accept_backoff_delay = 0.0
        transport = attach_stream_transport(reader, writer)
        session_key = id(transport)
        session: _ClientSession | None = None
        legacy_hello: HelloMessage | None = None
        try:
            raw_hello = await reader.readline()
            if not raw_hello:
                raise RuntimeError("client disconnected before hello")
            raw_object = json.loads(raw_hello)
            is_application = raw_object.get("op") == "client.hello"
            if is_application:
                app_hello = APPLICATION_WIRE_ADAPTER.validate_python(raw_object)
                if not isinstance(app_hello, ApplicationClientHello):
                    raise RuntimeError("first application message must be client.hello")
                if app_hello.protocol_version != APPLICATION_PROTOCOL_VERSION:
                    await self._send_application_error(
                        transport,
                        code=ApplicationErrorCode.VERSION_MISMATCH,
                        message=(
                            f"server={APPLICATION_PROTOCOL_VERSION} "
                            f"client={app_hello.protocol_version}"
                        ),
                    )
                    await transport.flush()
                    return
                kind = _application_client_kind(app_hello.client.kind)
                session = _ClientSession(
                    client_id=app_hello.client.client_id,
                    kind=kind,
                    transport=transport,
                    application=True,
                )
            else:
                legacy_message = WIRE_MESSAGE_ADAPTER.validate_python(raw_object)
                if not isinstance(legacy_message, HelloMessage):
                    raise RuntimeError("first message must be hello")
                legacy_hello = legacy_message
                if legacy_hello.body.protocol_version != PROTOCOL_VERSION:
                    await self._send_err(
                        transport,
                        correlation_id=legacy_hello.correlation_id,
                        code="protocol_version_mismatch",
                        message=(
                            f"server={PROTOCOL_VERSION} client={legacy_hello.body.protocol_version}"
                        ),
                    )
                    await transport.flush()
                    return
                if legacy_hello.body.client_kind in (ClientKind.TUI, ClientKind.WEB):
                    await self._send_err(
                        transport,
                        correlation_id=legacy_hello.correlation_id,
                        code="application_protocol_required",
                        message=("interactive clients must use the service application protocol"),
                    )
                    await transport.flush()
                    return
                session = _ClientSession(
                    client_id=legacy_hello.body.client_id,
                    kind=legacy_hello.body.client_kind,
                    transport=transport,
                )
            self._clients[session_key] = session
            if session.application:
                await self._send_message(
                    transport,
                    ApplicationServerHello(server_id=self._run_id),
                )
            else:
                assert legacy_hello is not None
                await self._send_ack(
                    transport,
                    correlation_id=legacy_hello.correlation_id,
                    kind="subscribed",
                )
                await self._send_wake(transport, legacy_hello.body.client_id)
            await self._on_connect(session.kind)
            while not reader.at_eof():
                line = await reader.readline()
                if not line:
                    break
                if session.application:
                    application_message = APPLICATION_WIRE_ADAPTER.validate_json(
                        line.decode("utf-8")
                    )
                    await self._handle_application_message(session, application_message)
                else:
                    bus_message = WIRE_MESSAGE_ADAPTER.validate_json(line.decode("utf-8"))
                    await self._handle_client_message(session, transport, bus_message)
        except Exception as exc:  # noqa: BLE001
            if not self._closed:
                LOGGER.warning("client connection failed: %s", exc, exc_info=True)
                with contextlib.suppress(Exception):
                    if session is not None and session.application:
                        await self._send_application_error(
                            transport,
                            code=ApplicationErrorCode.INVALID_MESSAGE,
                            message=str(exc),
                        )
                    else:
                        await self._send_err(
                            transport,
                            correlation_id="",
                            code="server_error",
                            message=str(exc),
                        )
                    await transport.flush()
        finally:
            if session is not None:
                for task in list(session.subscriptions):
                    task.cancel()
                for task in list(session.application_tasks.values()):
                    task.cancel()
                self._clients.pop(session_key, None)
                await self._on_disconnect(session.kind)
            await transport.close()

    def _on_subscription_done(self, session: _ClientSession, task: asyncio.Task[None]) -> None:
        """Retrieve a finished subscription task's outcome.

        A subscription that dies while its connection lives is a zombie: the
        client's state machine still believes it is subscribed, so it renders
        nothing forever (the original 'no chat history / waiting for tmux
        frame' failure). On an unexpected error, close the whole connection —
        the client observes EOF and reconnects with fresh subscriptions.
        """
        session.subscriptions.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is None or isinstance(exc, TransportClosedError):
            return
        LOGGER.warning(
            "subscription %s failed; closing client %s so it reconnects",
            task.get_name(),
            session.client_id,
            exc_info=exc,
        )
        asyncio.get_running_loop().create_task(session.transport.close())

    async def _read_hello(self, reader: asyncio.StreamReader) -> HelloMessage:
        raw = await reader.readline()
        if not raw:
            raise RuntimeError("client disconnected before hello")
        msg = WIRE_MESSAGE_ADAPTER.validate_json(raw.decode("utf-8"))
        if not isinstance(msg, HelloMessage):
            raise RuntimeError("first message must be hello")
        return msg

    async def _handle_client_message(
        self,
        session: _ClientSession | None,
        transport: UdsTransport,
        msg: Any,
    ) -> None:
        if isinstance(msg, SubMessage):
            if session is None:
                return
            task = asyncio.create_task(
                self._run_subscription(session, msg),
                name=f"bus-sub:{msg.correlation_id}",
            )
            session.subscriptions.add(task)
            task.add_done_callback(lambda t, s=session: self._on_subscription_done(s, t))
            return
        if isinstance(msg, PubMessage):
            await self._broker.publish(msg.event)
            await self._send_ack(
                transport,
                correlation_id=msg.correlation_id,
                kind="published",
            )
            return
        if isinstance(msg, RpcMessage):
            await self._handle_rpc(transport, msg)
            return
        if isinstance(msg, HydrateMessage):
            if session is None:
                return
            await self._handle_hydrate(session, msg)
            return
        await self._send_err(
            transport,
            correlation_id=msg.correlation_id,
            code="unsupported_op",
            message=f"unsupported op {msg.op}",
        )

    async def _handle_application_message(  # noqa: PLR0911
        self,
        session: _ClientSession,
        msg: Any,
    ) -> None:
        if isinstance(msg, ApplicationRequestMessage):
            try:
                result = await self._application_gateway.request(
                    msg.request,
                    timeout_s=msg.timeout_s,
                )
            except Exception as exc:  # noqa: BLE001
                await self._send_application_error(
                    session.transport,
                    code=ApplicationErrorCode.REQUEST_FAILED,
                    message=str(exc),
                    request_id=msg.request_id,
                )
                return
            await self._send_message(
                session.transport,
                ApplicationReplyMessage(request_id=msg.request_id, result=result),
            )
            return

        if isinstance(msg, ApplicationSubscribeMessage):
            await self._replace_application_task(
                session,
                msg.subscription_id,
                self._run_application_subscription(session, msg),
                error_target="subscription",
            )
            return

        if isinstance(msg, UnsubscribeMessage):
            await self._cancel_application_task(session, msg.subscription_id)
            return

        if isinstance(msg, TerminalAttachMessage):
            await self._cancel_application_task(session, msg.stream_id)
            session.application_terminals[msg.stream_id] = msg.target
            await self._replace_application_task(
                session,
                msg.stream_id,
                self._run_application_terminal_stream(session, msg),
                error_target="stream",
            )
            return

        if isinstance(msg, TerminalDetachMessage):
            session.application_terminals.pop(msg.stream_id, None)
            await self._cancel_application_task(session, msg.stream_id)
            return

        if isinstance(msg, TerminalResyncMessage):
            target = session.application_terminals.get(msg.stream_id)
            if target is None:
                await self._send_application_error(
                    session.transport,
                    code=ApplicationErrorCode.STREAM_FAILED,
                    message="terminal stream is not attached",
                    stream_id=msg.stream_id,
                )
                return
            await self._send_terminal_resync(
                session,
                stream_id=msg.stream_id,
                target=target,
                after_sequence=msg.after_sequence,
            )
            return

        await self._send_application_error(
            session.transport,
            code=ApplicationErrorCode.INVALID_MESSAGE,
            message=f"client cannot send {msg.op}",
        )

    async def _replace_application_task(
        self,
        session: _ClientSession,
        task_id: str,
        awaitable: Coroutine[Any, Any, None],
        *,
        error_target: Literal["subscription", "stream"],
    ) -> None:
        await self._cancel_application_task(session, task_id)
        task = asyncio.create_task(awaitable, name=f"app-stream:{task_id}")
        session.application_tasks[task_id] = task

        def _done(done: asyncio.Task[None]) -> None:
            if session.application_tasks.get(task_id) is done:
                session.application_tasks.pop(task_id, None)
            if done.cancelled():
                return
            exc = done.exception()
            if exc is not None and not isinstance(exc, TransportClosedError):
                LOGGER.warning("application stream %s failed", task_id, exc_info=exc)
                asyncio.get_running_loop().create_task(
                    self._fail_application_task(
                        session,
                        str(exc),
                        subscription_id=task_id if error_target == "subscription" else None,
                        stream_id=task_id if error_target == "stream" else None,
                    )
                )

        task.add_done_callback(_done)

    async def _fail_application_task(
        self,
        session: _ClientSession,
        message: str,
        *,
        subscription_id: str | None,
        stream_id: str | None,
    ) -> None:
        with contextlib.suppress(Exception):
            await self._send_application_error(
                session.transport,
                code=ApplicationErrorCode.STREAM_FAILED,
                message=message,
                subscription_id=subscription_id,
                stream_id=stream_id,
            )
        await session.transport.close()

    async def _cancel_application_task(
        self,
        session: _ClientSession,
        task_id: str,
    ) -> None:
        task = session.application_tasks.pop(task_id, None)
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _run_application_subscription(
        self,
        session: _ClientSession,
        msg: ApplicationSubscribeMessage,
    ) -> None:
        spec = msg.subscription
        if isinstance(spec, ProjectionSubscription):
            await self._run_application_projection_subscription(session, msg)
            return
        if isinstance(spec, NotificationSubscription):
            await self._run_application_notification_subscription(session, msg)
            return
        await self._send_application_error(
            session.transport,
            code=ApplicationErrorCode.UNSUPPORTED_SUBSCRIPTION,
            message=f"unsupported subscription {spec.kind}",
            subscription_id=msg.subscription_id,
        )

    async def _run_application_projection_subscription(
        self,
        session: _ClientSession,
        msg: ApplicationSubscribeMessage,
    ) -> None:
        spec = msg.subscription
        assert isinstance(spec, ProjectionSubscription)
        topics = [_legacy_hydrate_topic(topic.value) for topic in spec.topics]
        hydrate = HydrateMessage(
            correlation_id=msg.subscription_id,
            args={
                "topics": topics,
                "cursor": spec.cursor,
            },
        )
        reply, tail_filters = await self._build_hydrate_reply(hydrate)
        snapshots = {
            _application_projection_topic(key): value for key, value in reply.snapshots.items()
        }
        await self._send_message(
            session.transport,
            SubscriptionReadyMessage(
                subscription_id=msg.subscription_id,
                snapshot=SubscriptionSnapshot(
                    snapshots=snapshots,
                    cursor=reply.cursor,
                    mode=reply.mode,
                    replay=[
                        {
                            "cursor": item.seq,
                            "payload": item.event.model_dump(mode="json"),
                        }
                        for item in reply.replay
                    ],
                ),
            ),
        )
        await asyncio.gather(
            *[
                self._run_application_tail(
                    session.transport,
                    msg.subscription_id,
                    filt,
                    since_id=since_id,
                )
                for filt, since_id in tail_filters
            ]
        )

    async def _run_application_notification_subscription(
        self,
        session: _ClientSession,
        msg: ApplicationSubscribeMessage,
    ) -> None:
        spec = msg.subscription
        assert isinstance(spec, NotificationSubscription)
        watermark = self._broker.watermark()
        since_id = spec.cursor if spec.cursor is not None else watermark
        filters: list[EventFilter] = []
        if NotificationChannel.ERRORS in spec.channels:
            filters.append(EventFilter(type="error"))
        if NotificationChannel.PRESENCE in spec.channels:
            filters.append(EventFilter(type="presence"))
        await self._send_message(
            session.transport,
            SubscriptionReadyMessage(
                subscription_id=msg.subscription_id,
                snapshot=SubscriptionSnapshot(
                    cursor=watermark,
                    mode="resume" if spec.cursor is not None else "cold",
                ),
            ),
        )
        await asyncio.gather(
            *[
                self._run_application_tail(
                    session.transport,
                    msg.subscription_id,
                    filt,
                    since_id=since_id,
                )
                for filt in filters
            ]
        )

    async def _run_application_tail(
        self,
        transport: UdsTransport,
        subscription_id: str,
        filt: EventFilter,
        *,
        since_id: int,
    ) -> None:
        async for row_id, event in self._broker.tail(filt, since_id=since_id):
            await self._send_message(
                transport,
                SubscriptionEventMessage(
                    subscription_id=subscription_id,
                    cursor=row_id,
                    payload=event.model_dump(mode="json"),
                ),
            )

    async def _run_application_terminal_stream(
        self,
        session: _ClientSession,
        msg: TerminalAttachMessage,
    ) -> None:
        await self._send_message(
            session.transport,
            TerminalAttachedMessage(stream_id=msg.stream_id),
        )
        capture = self._tmux_frame_capture
        if capture is None:
            await self._send_application_error(
                session.transport,
                code=ApplicationErrorCode.STREAM_FAILED,
                message="terminal capture is unavailable",
                stream_id=msg.stream_id,
            )
            return
        after_sequence = msg.after_sequence
        while True:
            try:
                frame = await self._capture_terminal_replacement(
                    msg.target,
                    after_sequence=after_sequence,
                    subscription_id=msg.stream_id,
                )
            except Exception as exc:  # noqa: BLE001
                await self._send_application_error(
                    session.transport,
                    code=ApplicationErrorCode.STREAM_FAILED,
                    message=str(exc),
                    stream_id=msg.stream_id,
                )
                return
            after_sequence = frame.sequence
            await self._send_terminal_message(
                session.transport,
                TerminalFrameMessage(
                    stream_id=msg.stream_id,
                    frame=frame,
                ),
                stream_id=msg.stream_id,
            )
            await asyncio.sleep(self._tmux_frame_interval_s)

    async def _send_terminal_resync(
        self,
        session: _ClientSession,
        *,
        stream_id: str,
        target: TerminalTarget,
        after_sequence: int,
    ) -> None:
        try:
            frame = await self._capture_terminal_replacement(
                target,
                after_sequence=after_sequence,
                subscription_id=stream_id,
            )
        except Exception as exc:  # noqa: BLE001
            await self._send_application_error(
                session.transport,
                code=ApplicationErrorCode.STREAM_FAILED,
                message=str(exc),
                stream_id=stream_id,
            )
            return
        await self._send_terminal_message(
            session.transport,
            TerminalResyncedMessage(stream_id=stream_id, frame=frame),
            stream_id=stream_id,
        )

    async def _capture_terminal_replacement(
        self,
        target: TerminalTarget,
        *,
        after_sequence: int,
        subscription_id: str | None = None,
    ) -> TerminalFrame:
        capture = self._tmux_frame_capture
        if capture is None:
            raise RuntimeError("terminal capture is unavailable")
        target_ref = (
            str(target.session_id) if target.session_id is not None else target.legacy_agent_id
        )
        key = (
            (
                "session",
                str(target.session_id),
            )
            if target.session_id is not None
            else ("legacy", target.legacy_agent_id)
        )
        lock = self._terminal_capture_locks.setdefault(key, asyncio.Lock())
        async with lock:
            current = max(self._terminal_sequences.get(key, 0), after_sequence)
            captured = await capture(target_ref)
            sequence = current + 1
            self._terminal_sequences[key] = sequence
            if isinstance(captured, CapturedTerminalFrame):
                frame_text = captured.data
                columns = captured.columns
                rows = captured.rows
            else:
                # Compatibility for injected capture callbacks that predate
                # geometry provenance. Production never takes this branch.
                frame_text = captured
                columns, rows = _terminal_frame_geometry(frame_text)
            return TerminalFrame(
                subscription_id=subscription_id or "",
                sequence=sequence,
                session_id=target.session_id,
                legacy_agent_id=target.legacy_agent_id,
                captured_at=datetime.now(timezone.utc),
                columns=columns,
                rows=rows,
                data=frame_text,
                reset=True,
            )

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

    async def _handle_hydrate(self, session: _ClientSession, msg: HydrateMessage) -> None:
        try:
            reply, tail_filters = await self._build_hydrate_reply(msg)
        except Exception as exc:  # noqa: BLE001
            await self._send_err(
                session.transport,
                correlation_id=msg.correlation_id,
                code="hydrate_error",
                message=str(exc),
            )
            return

        await self._send_ack(
            session.transport,
            correlation_id=msg.correlation_id,
            kind="hydrate_reply",
            watermark=reply.cursor,
            result=reply.model_dump(mode="json"),
        )

        for filt, since_id in tail_filters:
            task = asyncio.create_task(
                self._run_hydrate_tail(session, msg.correlation_id, filt, since_id=since_id),
                name=f"bus-hydrate-tail:{msg.correlation_id}:{filt.type or 'all'}",
            )
            session.subscriptions.add(task)
            task.add_done_callback(lambda t, s=session: self._on_subscription_done(s, t))

    async def _build_hydrate_reply(
        self,
        msg: HydrateMessage,
    ) -> tuple[HydrateReply, list[tuple[EventFilter, int]]]:
        requested = _normalize_hydrate_topics(msg.args.topics)
        watermark = self._broker.watermark()
        cursor = msg.args.cursor
        retained = cursor is not None and self._cursor_is_retained(cursor, watermark)

        if retained and cursor is not None:
            replay_filters = _hydrate_replay_filters(requested)
            replay = self._hydrate_replay(cursor=cursor, until_id=watermark, filters=replay_filters)
            reply = HydrateReply(
                snapshots={},
                cursor=watermark,
                mode="resume",
                replay=replay,
            )
            return reply, _hydrate_tail_filters(requested, since_id=watermark)

        snapshots: dict[str, dict[str, Any]] = {}
        for topic in requested:
            target = _HYDRATE_SNAPSHOT_TARGETS.get(topic)
            if target is None:
                continue
            snapshots[topic] = await self._broker.request(
                target,
                {},
                timeout_s=msg.args.timeout_s,
            )
        reply = HydrateReply(
            snapshots=snapshots,
            cursor=watermark,
            mode="cold" if cursor is None else "snapshot_fallback",
        )
        return reply, _hydrate_tail_filters(requested, since_id=watermark)

    def _cursor_is_retained(self, cursor: int, watermark: int) -> bool:
        if cursor < 0 or cursor > watermark:
            return False
        for name in ("is_cursor_retained", "cursor_retained"):
            checker = getattr(self._broker, name, None)
            if checker is not None:
                return bool(checker(cursor))
        # Current DurableBroker has no retention/pruning implementation, so a
        # cursor not ahead of the watermark is replayable. When retention lands,
        # expose one of the methods above so stale cursors degrade to snapshots.
        return True

    def _hydrate_replay(
        self,
        *,
        cursor: int,
        until_id: int,
        filters: list[EventFilter],
    ) -> list[HydrateReplayEvent]:
        out: list[HydrateReplayEvent] = []
        seen: set[int] = set()
        for filt in filters:
            for row_id, event in self._broker.replay(
                filt,
                since_id=cursor,
                until_id=until_id,
            ):
                if row_id in seen:
                    continue
                seen.add(row_id)
                out.append(HydrateReplayEvent(seq=row_id, event=event))
        out.sort(key=lambda item: item.seq)
        return out

    async def _run_hydrate_tail(
        self,
        session: _ClientSession,
        correlation_id: str,
        filt: EventFilter,
        *,
        since_id: int,
    ) -> None:
        async for row_id, event in self._broker.tail(filt, since_id=since_id):
            await self._send_pub(session.transport, correlation_id, event, seq=row_id)

    async def _run_subscription(self, session: _ClientSession, msg: SubMessage) -> None:
        filt = msg.args.filter
        watermark = self._broker.watermark()
        transport = session.transport
        await self._send_ack(
            transport,
            correlation_id=msg.correlation_id,
            kind="subscribed",
        )

        # tmux.frame subscriptions bypass the broker entirely: no DB persistence,
        # no replay, no fan-out.  We complete the normal handshake (replay_done with
        # the current watermark) so the client's subscription state machine finishes,
        # then enter a capture loop that runs only for the lifetime of this task.
        # Cancelling the task (on unsubscribe / disconnect / server stop) stops the
        # loop immediately — zero standing cost when nobody is subscribed.
        if filt.type == "tmux.frame":
            await self._send_ack(
                transport,
                correlation_id=msg.correlation_id,
                kind="replay_done",
                watermark=watermark,
            )
            await self._run_tmux_frame_stream(transport, msg.correlation_id, agent_id=filt.agent_id)
            return

        replay_since = watermark if msg.args.tail_only else (msg.args.since_id or 0)
        for row_id, event in self._broker.replay(
            filt,
            since_id=replay_since,
            until_id=watermark,
        ):
            await self._send_pub(transport, msg.correlation_id, event, seq=row_id)
        await self._send_ack(
            transport,
            correlation_id=msg.correlation_id,
            kind="replay_done",
            watermark=watermark,
        )
        if msg.args.presence_retain:
            # Synthesised from live in-memory state, so its version may be
            # higher than a presence row persisted in the replay window — and
            # tail (below) may then redeliver that older persisted row out of
            # order. That is safe ONLY because PresenceEvent carries a monotonic
            # ``version`` and subscribers MUST drop any non-strictly-greater
            # version (protocol.py). No other event kind tolerates the seam, so
            # presence_retain must stay presence-only.
            retained = self._presence_event()
            if filt.matches(retained):
                await self._send_pub(transport, msg.correlation_id, retained)
        async for row_id, event in self._broker.tail(filt, since_id=watermark):
            await self._send_pub(transport, msg.correlation_id, event, seq=row_id)

    async def _run_tmux_frame_stream(
        self,
        transport: UdsTransport,
        correlation_id: str,
        agent_id: str | None = None,
    ) -> None:
        """Capture-poll loop for one tmux pane (``agent_id``'s session, or the
        service's own session when the filter carries no agent).

        Runs for the lifetime of the subscription task.  The loop is
        cancelled (and therefore closed) when:
        - the client disconnects (``_handle_client`` finally cancels tasks);
        - the server shuts down (``stop()`` cancels tasks);
        - a future ``unsub`` wire op is added (not yet in the protocol).

        If no ``tmux_frame_capture`` was injected, the loop exits immediately
        (no-op) so tests that don't care about frame content still pass.
        """
        capture = self._tmux_frame_capture
        if capture is None:
            return
        while True:
            try:
                captured = await capture(agent_id)
                frame_text = (
                    captured.data if isinstance(captured, CapturedTerminalFrame) else captured
                )
            except Exception as exc:
                # tmux not running, session gone, etc. Surface the failure as
                # the frame itself: the raw view is the parsing *backup*, so an
                # eternal '[waiting for tmux frame…]' hides exactly the state
                # the user opened it to inspect.
                frame_text = f"[tmux capture failed: {exc}]"
            event = TmuxFrameEvent(
                run_id=self._run_id,
                agent_id=agent_id or "supervisor",
                frame=frame_text,
            )
            # Legacy subscribers retain their envelope during the migration,
            # but terminal bytes never enter the lossless control queue.
            await self._send_terminal_message(
                transport,
                PubMessage(correlation_id=correlation_id, event=event),
                stream_id=f"legacy-tmux:{correlation_id}",
            )
            await asyncio.sleep(self._tmux_frame_interval_s)

    async def _send_pub(
        self,
        transport: UdsTransport,
        correlation_id: str,
        event: Any,
        *,
        seq: int | None = None,
    ) -> None:
        await self._send_message(
            transport,
            PubMessage(correlation_id=correlation_id, event=event, seq=seq),
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

    async def _send_application_error(
        self,
        transport: UdsTransport,
        *,
        code: ApplicationErrorCode,
        message: str,
        request_id: str | None = None,
        subscription_id: str | None = None,
        stream_id: str | None = None,
    ) -> None:
        await self._send_message(
            transport,
            ApplicationErrorMessage(
                request_id=request_id,
                subscription_id=subscription_id,
                stream_id=stream_id,
                error=ApplicationErrorBody(code=code, message=message),
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

    async def _send_terminal_message(
        self,
        transport: UdsTransport,
        message: Any,
        *,
        stream_id: str,
    ) -> None:
        """Queue terminal traffic independently with latest-frame-wins semantics."""

        wire = message.model_dump(mode="json")
        payload = (json.dumps(wire, default=str) + "\n").encode("utf-8")
        send_terminal = getattr(transport, "send_terminal", None)
        if send_terminal is None:
            # Lightweight test doubles and transitional transports still expose
            # only the base Transport interface.
            await transport.send(payload)
            return
        await send_terminal(payload, stream_id=stream_id)

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


def default_socket_path(repo_root: Path | None = None) -> Path:
    return socket_path_for_repo(repo_root or Path.cwd())


def _is_loopback_host(host: str) -> bool:
    """True if *host* is unambiguously loopback (refuse anything else for TCP)."""
    if host in ("localhost", ""):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        # Hostnames other than 'localhost' may resolve anywhere — treat as
        # non-loopback rather than doing a DNS lookup here.
        return False


def _application_client_kind(kind: ApplicationClientKind) -> ClientKind:
    if kind is ApplicationClientKind.TUI:
        return ClientKind.TUI
    if kind is ApplicationClientKind.WEB:
        return ClientKind.WEB
    return ClientKind.CLI_EPHEMERAL


def _legacy_hydrate_topic(topic: str) -> str:
    return "crow" if topic == "roster" else topic


def _application_projection_topic(topic: str) -> str:
    return "roster" if topic == "crow" else topic


def _normalize_hydrate_topics(topics: list[str]) -> list[str]:
    requested = [str(topic).strip() for topic in topics if str(topic).strip()]
    if not requested or "all" in requested:
        return list(_HYDRATE_ALL_TOPICS)
    out: list[str] = []
    seen: set[str] = set()
    valid = set(_HYDRATE_SNAPSHOT_TARGETS) | {"state", "events", "errors"}
    for topic in requested:
        if topic not in valid:
            raise ValueError(f"unsupported hydrate topic {topic!r}")
        if topic not in seen:
            seen.add(topic)
            out.append(topic)
    return out


def _hydrate_replay_filters(topics: list[str]) -> list[EventFilter]:
    filters: list[EventFilter] = []
    if any(topic in topics for topic in ("state", "crow", "schedule")):
        filters.append(EventFilter(type="state.snapshot"))
    if "conversations" in topics or "events" in topics:
        filters.append(EventFilter(type="conversation.block"))
        filters.append(EventFilter(type="conversation.state"))
    return filters


def _hydrate_tail_filters(topics: list[str], *, since_id: int) -> list[tuple[EventFilter, int]]:
    filters = [(filt, since_id) for filt in _hydrate_replay_filters(topics)]
    if "errors" in topics or set(topics) == set(_HYDRATE_ALL_TOPICS):
        filters.append((EventFilter(type="error"), since_id))
    return filters


# ---------------------------------------------------------------------------
# Client-side UDS Transport
# ---------------------------------------------------------------------------

from murder.bus.transport import Transport  # noqa: E402,I001


_SENTINEL = object()  # signals the writer drain loop to stop


@dataclass(frozen=True)
class _TerminalWrite:
    payload: bytes
    overflow_gap: bytes | None = None
    is_gap: bool = False


class UdsTransport(Transport):
    """Async client-side Unix-domain-socket transport.

    Design choices
    --------------
    * **Independent logical queues** — control traffic uses a bounded,
      lossless queue while terminal frames use a separate bounded,
      latest-frame-wins map.  A dedicated ``_drain_loop`` remains the sole
      coroutine that writes and always checks control traffic first.

    * **Two distinct timeouts**:
      - ``rpc_timeout`` — applied to short request/response exchanges
        (handshake, RPC, ack).  Default 30 s.
      - ``subscription_idle_timeout`` — applied to quiet subscription
        streams.  If no data arrives for this long the connection is
        closed.  Default 300 s.

    * **Control backpressure** — the control queue has a bounded capacity
      (``_WRITE_QUEUE_MAX``).  ``send()`` blocks while the queue is full,
      throttling bursty producers (e.g. a subscription replaying thousands
      of broker events without yielding) to socket drain speed.  It never
      raises on a full queue: the previous fail-fast behaviour silently
      killed server-side subscription tasks on every large replay, leaving
      clients connected but receiving nothing. Terminal producers never
      block control traffic: ``send_terminal`` replaces a pending frame for
      the same stream and drops the oldest stream when its own bound is hit.
    """

    _WRITE_QUEUE_MAX = 256
    _TERMINAL_QUEUE_MAX = 32

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
        self._terminal_frames: OrderedDict[str, _TerminalWrite] = OrderedDict()
        self._terminal_ready = asyncio.Event()
        self._terminal_frames_dropped = 0
        self._drain_task: asyncio.Task[None] | None = None
        self._connected = False

    @classmethod
    def from_streams(
        cls,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        *,
        subscription_idle_timeout: float = 300.0,
    ) -> UdsTransport:
        """Build a server-side transport around an already-accepted stream pair.

        The client path uses :meth:`connect`; on the server an accepted
        connection arrives as a ``(reader, writer)`` pair, so this is the
        sanctioned way to wrap it without poking private attributes.
        """
        transport = cls(subscription_idle_timeout=subscription_idle_timeout)
        transport._reader = reader
        transport._writer = writer
        transport._connected = True
        transport._drain_task = asyncio.create_task(
            transport._drain_loop(), name="uds-stream-transport-drain"
        )
        return transport

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(self, path: str | Path) -> None:
        """Open a connection to the UDS at *path*."""
        reader, writer = await asyncio.open_unix_connection(str(path))
        self._reader = reader
        self._writer = writer
        self._connected = True
        self._drain_task = asyncio.create_task(self._drain_loop(), name="uds-transport-drain")

    async def _drain_loop(self) -> None:
        """Single writer coroutine with strict control-queue preference."""
        assert self._writer is not None
        writer = self._writer
        try:
            while True:
                item = await self._next_write()
                if item is _SENTINEL:
                    break
                if isinstance(item, asyncio.Event):
                    item.set()
                    continue
                assert isinstance(item, bytes)
                try:
                    writer.write(item)
                    await writer.drain()
                except Exception:
                    self._connected = False
                    raise
        finally:
            self._connected = False

    async def _next_write(self) -> bytes | object:
        """Select control first, then the latest pending terminal frames."""

        while True:
            try:
                return self._write_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass

            if self._terminal_frames:
                _stream_id, terminal = self._terminal_frames.popitem(last=False)
                if not self._terminal_frames:
                    self._terminal_ready.clear()
                return terminal.payload

            control_wait = asyncio.create_task(self._write_queue.get())
            terminal_wait = asyncio.create_task(self._terminal_ready.wait())
            try:
                done, _pending = await asyncio.wait(
                    {control_wait, terminal_wait},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if control_wait in done:
                    return control_wait.result()
            finally:
                for task in (control_wait, terminal_wait):
                    if not task.done():
                        task.cancel()
                await asyncio.gather(
                    control_wait,
                    terminal_wait,
                    return_exceptions=True,
                )

    # ------------------------------------------------------------------
    # Transport ABC
    # ------------------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def send(self, data: bytes) -> None:
        """Enqueue *data* for the drain loop.

        Blocks while the queue is at capacity (backpressure), pacing the
        caller to socket drain speed. A genuinely stuck peer is handled at
        the connection level: the reader side times out / EOFs, the
        connection handler cancels its subscription tasks, and that
        cancellation propagates into any ``put`` blocked here.
        """
        if not self._connected:
            raise TransportClosedError("transport is not connected")
        await self._write_queue.put(data)

    async def send_terminal(
        self,
        data: bytes,
        *,
        stream_id: str,
        overflow_gap: bytes | None = None,
    ) -> None:
        """Enqueue a terminal frame without consuming control-queue capacity.

        At most one unsent update is retained per stream, so a fast tmux
        capture loop cannot build stale history. When more distinct streams
        than the terminal bound are pending, the oldest pending stream is
        evicted; its next full replacement frame recovers the client.

        Full frames omit ``overflow_gap`` and latest wins. A future
        incremental producer must supply its serialized ``terminal.gap``;
        coalescing then retains that gap instead of an unsafe later chunk.
        """

        if not self._connected:
            raise TransportClosedError("transport is not connected")
        incoming = _TerminalWrite(data, overflow_gap=overflow_gap)
        existing = self._terminal_frames.get(stream_id)
        if existing is not None:
            self._terminal_frames_dropped += 1
            if overflow_gap is None:
                # An authoritative full frame recovers any pending gap/chunk.
                self._terminal_frames[stream_id] = incoming
            elif existing.is_gap:
                # Do not let later chunks overwrite the required recovery.
                pass
            else:
                gap = existing.overflow_gap or overflow_gap
                self._terminal_frames[stream_id] = _TerminalWrite(gap, is_gap=True)
            self._terminal_frames.move_to_end(stream_id)
        elif len(self._terminal_frames) >= self._TERMINAL_QUEUE_MAX:
            self._terminal_frames_dropped += 1
            oldest_id, oldest = next(iter(self._terminal_frames.items()))
            if oldest.is_gap:
                pass
            elif oldest.overflow_gap is not None:
                self._terminal_frames[oldest_id] = _TerminalWrite(
                    oldest.overflow_gap,
                    is_gap=True,
                )
                self._terminal_frames.move_to_end(oldest_id)
            else:
                self._terminal_frames.popitem(last=False)
                self._terminal_frames[stream_id] = incoming
        else:
            self._terminal_frames[stream_id] = incoming
        self._terminal_ready.set()

    async def flush(self) -> None:
        """Wait until every message queued before this call has been written."""

        if not self._connected:
            return
        marker = asyncio.Event()
        await self._write_queue.put(marker)
        await asyncio.wait_for(marker.wait(), timeout=self.rpc_timeout)

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
        # Release any senders blocked on a full queue: with the drain loop gone
        # nothing else will ever pop items, so their pending ``put`` calls would
        # otherwise never resolve. The data is dropped — the peer is gone.
        while True:
            try:
                self._write_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        self._terminal_frames.clear()
        self._terminal_ready.clear()
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
    return UdsTransport.from_streams(
        reader, writer, subscription_idle_timeout=subscription_idle_timeout
    )


# ---------------------------------------------------------------------------
# Transport-layer exceptions
# ---------------------------------------------------------------------------


class TransportError(RuntimeError):
    """Base class for UdsTransport errors."""


class TransportClosedError(TransportError):
    """Raised when an operation is attempted on a closed transport."""


__all__ = [
    "UdsTransport",
    "attach_stream_transport",
    "TransportError",
    "TransportClosedError",
]
