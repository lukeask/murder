from __future__ import annotations

import asyncio
import contextlib
import errno
import ipaddress
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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

# Callable that returns the current ANSI frame for an agent's pane (or the
# service's own session when ``agent_id`` is None). Injected into
# SocketBusServer so tests can supply a controllable fake.
TmuxFrameCapture = Callable[[str | None], Awaitable[str]]

# Default interval between frame captures (seconds).  Chosen to be responsive
# without hammering tmux; Ink renders at terminal frame-rate so 100ms is plenty.
TMUX_FRAME_INTERVAL_S = 0.1


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
        tmux_frame_capture: TmuxFrameCapture | None = None,
        tmux_frame_interval_s: float = TMUX_FRAME_INTERVAL_S,
    ) -> None:
        self._broker = broker
        self._run_id = run_id
        self._socket_path = socket_path or default_socket_path()
        self._disconnect_debounce_s = disconnect_debounce_s
        self._tmux_frame_capture = tmux_frame_capture
        self._tmux_frame_interval_s = tmux_frame_interval_s
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

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        self._accept_backoff_delay = 0.0
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
                await self._handle_client_message(session, transport, msg)
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
            await self._run_tmux_frame_stream(
                transport, msg.correlation_id, agent_id=filt.agent_id
            )
            return

        replay_since = (
            watermark if msg.args.tail_only else (msg.args.since_id or 0)
        )
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
                frame_text = await capture(agent_id)
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
            await self._send_pub(transport, correlation_id, event)
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
      (``_WRITE_QUEUE_MAX``).  ``send()`` blocks while the queue is full,
      throttling bursty producers (e.g. a subscription replaying thousands
      of broker events without yielding) to socket drain speed.  It never
      raises on a full queue: the previous fail-fast behaviour silently
      killed server-side subscription tasks on every large replay, leaving
      clients connected but receiving nothing.
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

        Blocks while the queue is at capacity (backpressure), pacing the
        caller to socket drain speed. A genuinely stuck peer is handled at
        the connection level: the reader side times out / EOFs, the
        connection handler cancels its subscription tasks, and that
        cancellation propagates into any ``put`` blocked here.
        """
        if not self._connected:
            raise TransportClosedError("transport is not connected")
        await self._write_queue.put(data)

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
