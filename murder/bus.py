"""Typed event bus (D4).

`AgentEvent` is a discriminated union of pydantic models. Subscribers
register handlers; the bus persists every event to the SQLite `events`
table before fanning out (so a subscriber crash never loses an event).

Single-process asyncio; no external broker (D1).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from enum import Enum

try:
    from enum import StrEnum
except ImportError:  # Python <3.11
    class StrEnum(str, Enum):
        def __str__(self) -> str:
            return str.__str__(self)
from typing import TYPE_CHECKING, Annotated, Any, Literal, Union
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, TypeAdapter

if TYPE_CHECKING:
    import sqlite3

log = logging.getLogger("murder.bus")


class Role(StrEnum):
    COLLABORATOR = "collaborator"
    SENTINEL = "sentinel"
    AUGUR = "augur"
    MONKEY = "monkey"


class TicketStatus(StrEnum):
    PLANNED = "planned"
    READY = "ready"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    DONE = "done"
    FAILED = "failed"


class AgentStatus(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    BLOCKED = "blocked"
    ESCALATING = "escalating"
    DONE = "done"
    FAILED = "failed"
    DEAD = "dead"


class _BaseEvent(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    ts: datetime = Field(default_factory=datetime.utcnow)
    run_id: str
    agent_id: str
    role: Role
    ticket_id: str | None = None


class HeartbeatEvent(_BaseEvent):
    type: Literal["heartbeat"] = "heartbeat"
    state: Literal["progressing", "stuck", "thinking"]
    summary: str | None = None
    since_change_s: int = 0


class SummaryEvent(_BaseEvent):
    type: Literal["summary"] = "summary"
    text: str
    checklist_done: int = 0
    checklist_total: int = 0
    last_message_excerpt: str = ""


class QuestionEvent(_BaseEvent):
    type: Literal["question"] = "question"
    question: str
    monkey_session: str
    recent_pane: str = ""


class EscalationEvent(_BaseEvent):
    type: Literal["escalation"] = "escalation"
    to: Literal["user", "collaborator"]
    reason: str
    severity: Literal[1, 2, 3] = 2
    monkey_session: str | None = None
    source_event_id: UUID | None = None


class StatusChangeEvent(_BaseEvent):
    type: Literal["status_change"] = "status_change"
    entity: Literal["agent", "ticket"]
    entity_id: str
    from_status: str
    to_status: str
    reason: str | None = None


class ErrorEvent(_BaseEvent):
    type: Literal["error"] = "error"
    message: str
    recoverable: bool = True
    traceback: str | None = None


AgentEvent = Annotated[
    Union[
        HeartbeatEvent,
        SummaryEvent,
        QuestionEvent,
        EscalationEvent,
        StatusChangeEvent,
        ErrorEvent,
    ],
    Field(discriminator="type"),
]

_EVENT_ADAPTER: TypeAdapter[AgentEvent] = TypeAdapter(AgentEvent)


class EventFilter(BaseModel):
    role: Role | None = None
    ticket_id: str | None = None
    type: str | None = None

    def matches(self, event: _BaseEvent) -> bool:
        if self.role is not None and event.role != self.role:
            return False
        if self.ticket_id is not None and event.ticket_id != self.ticket_id:
            return False
        if self.type is not None and getattr(event, "type", None) != self.type:
            return False
        return True


Handler = Callable[[Any], Awaitable[None]]  # Any here = AgentEvent union member


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
            from murder.db import insert_event

            payload = event.model_dump(mode="json", exclude={"run_id", "agent_id", "role", "ticket_id", "ts", "id"})
            try:
                insert_event(
                    self._db,
                    run_id=event.run_id,
                    agent_id=event.agent_id,
                    role=event.role.value if hasattr(event.role, "value") else str(event.role),
                    ticket_id=event.ticket_id,
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
