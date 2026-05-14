"""In-process pub/sub broker + back-compat re-exports.

The wire contract (event types, envelope, constants) lives in
``murder.bus.protocol`` — see that module for the frozen schema both
branches build against during the worker-bus refactor.

This module keeps the in-process ``Bus`` broker used by the current
single-process runtime. The broker persists every event to the SQLite
``events`` table before fanning out, so handler crashes never lose
events. Existing call sites continue to import legacy names
(``Role``, ``TicketStatus``, ``HeartbeatEvent``, …) from
``murder.bus``; they're re-exported here.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from murder.bus.protocol import (
    AckBody,
    AckMessage,
    AgentEvent,
    AgentStatus,
    BUS_EVENT_ADAPTER,
    BusEvent,
    ClientKind,
    CommandEvent,
    CommandStatus,
    DEFAULT_HEARTBEAT_INTERVAL_S,
    DEFAULT_LEASE_TTL_S,
    DEFAULT_MAX_COMMAND_ATTEMPTS,
    DEFAULT_RPC_TIMEOUT_S,
    Entity,
    ErrBody,
    ErrMessage,
    ErrorEvent,
    EscalationEvent,
    EventFilter,
    HeartbeatEvent,
    HelloBody,
    HelloMessage,
    IDEMPOTENCY_WINDOW_S,
    PROTOCOL_VERSION,
    PRESENCE_DISCONNECT_DEBOUNCE_S,
    PRESENCE_USER_KINDS,
    PresenceEvent,
    PresenceState,
    PubMessage,
    QuestionEvent,
    Role,
    RpcArgs,
    RpcMessage,
    SOCKET_BASENAME,
    SOCKET_RUNTIME_SUBDIR,
    SUBSCRIBER_QUEUE_DEFAULT,
    StateSnapshotEvent,
    StatusChangeEvent,
    SubArgs,
    SubMessage,
    SummaryEvent,
    TicketStatus,
    WIRE_MESSAGE_ADAPTER,
    WakeBody,
    WakeMessage,
    WireMessage,
)

if TYPE_CHECKING:
    import sqlite3

log = logging.getLogger("murder.bus")


Handler = Callable[[Any], Awaitable[None]]


class SubscriptionHandle:
    def __init__(self, bus: "Bus", token: int) -> None:
        self._bus = bus
        self._token = token
        self._cancelled = False

    def cancel(self) -> None:
        if self._cancelled:
            return
        self._cancelled = True
        self._bus._subs.pop(self._token, None)


class Bus:
    """In-process pub/sub. Persists every event before fan-out."""

    def __init__(self, run_id: str, db_conn: "sqlite3.Connection | None" = None) -> None:
        self._run_id = run_id
        self._db = db_conn
        self._subs: dict[int, tuple[Handler, EventFilter | None]] = {}
        self._next_token = 0
        self._lock = asyncio.Lock()

    async def publish(self, event: Any) -> None:
        # Persist before fan-out so handler crashes can't lose events.
        if self._db is not None:
            from murder.db import insert_command_event, insert_event

            payload = event.model_dump(
                mode="json",
                exclude={"run_id", "agent_id", "role", "ticket_id", "ts", "id"},
            )
            role_value: str | None = None
            ev_role = getattr(event, "role", None)
            if ev_role is not None:
                role_value = ev_role.value if hasattr(ev_role, "value") else str(ev_role)
            try:
                if isinstance(event, CommandEvent):
                    insert_command_event(
                        self._db,
                        command_id=str(event.id),
                        run_id=event.run_id,
                        agent_id=event.agent_id,
                        role=role_value,
                        ticket_id=getattr(event, "ticket_id", None),
                        target_worker=event.target_worker,
                        kind=event.kind,
                        payload=event.payload,
                        correlation_id=event.correlation_id,
                        idempotency_key=event.idempotency_key,
                        status=event.status.value,
                        claimed_by=event.claimed_by,
                        lease_expires_at=event.lease_expires_at,
                        attempt_count=event.attempt_count,
                        retryable=event.retryable,
                        result=event.result,
                        event_type=event.type,
                        event_payload=payload,
                        ts=event.ts.isoformat(timespec="seconds"),
                    )
                else:
                    insert_event(
                        self._db,
                        run_id=event.run_id,
                        agent_id=event.agent_id,
                        role=role_value or "",
                        ticket_id=getattr(event, "ticket_id", None),
                        type=event.type,
                        payload=payload,
                        ts=event.ts.isoformat(timespec="seconds"),
                    )
            except Exception:
                log.exception("bus: failed to persist event %s", event.type)
                # Continue with fan-out even if persistence failed.

        async with self._lock:
            handlers = list(self._subs.values())

        # Fan out concurrently; each handler isolated.
        async def _dispatch(h: Handler, f: EventFilter | None) -> None:
            if f is not None and not f.matches(event):
                return
            try:
                await h(event)
            except Exception:
                log.exception("bus: handler raised on %s", event.type)

        await asyncio.gather(*(_dispatch(h, f) for h, f in handlers))

    def subscribe(
        self, handler: Handler, filter: EventFilter | None = None
    ) -> SubscriptionHandle:
        token = self._next_token
        self._next_token += 1
        self._subs[token] = (handler, filter)
        return SubscriptionHandle(self, token)


__all__ = [
    # Broker implementation
    "Bus", "SubscriptionHandle", "Handler",
    # Re-exports from protocol
    "PROTOCOL_VERSION",
    "Role", "TicketStatus", "AgentStatus", "CommandStatus",
    "Entity", "PresenceState", "ClientKind",
    "HeartbeatEvent", "SummaryEvent", "QuestionEvent",
    "EscalationEvent", "StatusChangeEvent", "ErrorEvent",
    "CommandEvent", "StateSnapshotEvent", "PresenceEvent",
    "BusEvent", "AgentEvent", "BUS_EVENT_ADAPTER",
    "EventFilter",
    "HelloBody", "SubArgs", "RpcArgs", "AckBody", "ErrBody", "WakeBody",
    "HelloMessage", "PubMessage", "SubMessage", "RpcMessage",
    "AckMessage", "ErrMessage", "WakeMessage",
    "WireMessage", "WIRE_MESSAGE_ADAPTER",
    "SOCKET_RUNTIME_SUBDIR", "SOCKET_BASENAME",
    "DEFAULT_RPC_TIMEOUT_S", "DEFAULT_HEARTBEAT_INTERVAL_S",
    "DEFAULT_LEASE_TTL_S", "DEFAULT_MAX_COMMAND_ATTEMPTS",
    "PRESENCE_DISCONNECT_DEBOUNCE_S", "PRESENCE_USER_KINDS",
    "SUBSCRIBER_QUEUE_DEFAULT", "IDEMPOTENCY_WINDOW_S",
]
