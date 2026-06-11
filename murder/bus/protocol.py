"""Frozen wire contract between supervisor, workers, and clients.

This module is the single source of truth for the bus protocol that spans
the worker-bus refactor. Both the TUI branch and the backend branch import
from here. The TUI develops against a fake bus that produces messages
matching these types; the backend builds the broker that produces them
for real. They meet at this file.

Amendment process
-----------------
- **Additive changes** (new optional fields, new BusEvent kinds, new
  closed-enum values that don't change existing meaning) MAY be made
  inline. Coordinate the PR across both branches.
- **Non-additive changes** (renaming a field, changing a discriminator
  value, narrowing a type, removing a kind) MUST bump
  ``PROTOCOL_VERSION``. Clients refuse mismatched versions on connect.

Out of scope
------------
Broker implementations, transport framing read/write loops, kind handler
bodies, asyncio glue, sqlite glue. Types and constants only. If you find
yourself importing ``asyncio`` here, you're in the wrong file.

See `.agents/bus_protocol.md` for the design rationale behind these
shapes.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

try:
    from enum import StrEnum
except ImportError:  # Python <3.11

    class StrEnum(str, Enum):
        def __str__(self) -> str:
            return str.__str__(self)


from pydantic import BaseModel, Field, TypeAdapter

from murder.work.tickets.status import TicketStatus

PROTOCOL_VERSION = 3


# === Closed enums ============================================================


class Role(StrEnum):
    COLLABORATOR = "collaborator"
    NOTETAKER = "notetaker"
    PLANNER = "planner"
    PLANNING_HANDLER = "planning_handler"
    CROW_HANDLER = "crow_handler"
    CROW = "crow"


class AgentStatus(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    BLOCKED = "blocked"
    ESCALATING = "escalating"
    DONE = "done"
    FAILED = "failed"
    DEAD = "dead"


class CommandStatus(StrEnum):
    PENDING = "pending"
    IN_FLIGHT = "in_flight"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Entity(StrEnum):
    """Closed — adding a value bumps PROTOCOL_VERSION."""

    TICKET = "ticket"
    AGENT = "agent"
    PLAN = "plan"
    NOTE = "note"
    REPORT = "report"
    ESCALATION = "escalation"
    QUEUE_ROW = "queue_row"


class PresenceState(StrEnum):
    ATTENDED = "attended"
    HEADLESS = "headless"


class ClientKind(StrEnum):
    TUI = "tui"
    WEB = "web"
    CLI_EPHEMERAL = "cli_ephemeral"
    WORKER = "worker"


# === Inner events (persisted to events table; discriminated by `type`) =======


class _BaseEvent(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    ts: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    run_id: str
    agent_id: str = ""
    role: Role | None = None
    ticket_id: str | None = None


# --- Legacy types (semantics unchanged from murder/bus.py pre-refactor) ------


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
    crow_session: str
    recent_pane: str = ""


class NoteEvent(_BaseEvent):
    """A free-text working note emitted by a crow via a ``>>> NOTE:`` marker.

    Under DB-owns-runtime these land in the ``events`` table (audit log), not
    the ticket ``.md`` — the bus persists every event before fan-out.
    """

    type: Literal["note"] = "note"
    note: str


class EscalationEvent(_BaseEvent):
    type: Literal["escalation"] = "escalation"
    to: Literal["user", "collaborator"]
    reason: str
    severity: Literal[1, 2, 3] = 2
    crow_session: str | None = None
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


# --- New types added by the worker-bus refactor -----------------------------


class CommandEvent(_BaseEvent):
    """A request to a worker. Lives in both ``commands`` and ``events``.

    The ``commands`` row is the work queue; the ``events`` row is the
    audit log. Both are written in one transaction. ``events.payload_json``
    carries enough to reconstruct the command id; the full body lives in
    ``commands``.
    """

    type: Literal["command"] = "command"
    target_worker: str
    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str
    idempotency_key: str
    status: CommandStatus = CommandStatus.PENDING
    claimed_by: str | None = None
    lease_expires_at: int | None = None
    attempt_count: int = 0
    retryable: bool = True
    result: dict[str, Any] | None = None


class StateSnapshotEvent(_BaseEvent):
    """Key-only notification that an entity changed.

    Body lives in SQLite; every client holds a read-only DB handle and
    refetches on receipt. See worker_bus_refactor.md §D.5 for the
    deliberation.
    """

    type: Literal["state.snapshot"] = "state.snapshot"
    entity: Entity
    key: str
    entity_version: int = 0
    # Reserved forward-compat field (F1, unused now). The bus->client contract
    # stays key-only: default behaviour is a tiny key-only push and the client
    # refetches the named slice. A future low-bandwidth mode MAY inline the
    # changed data here (paired with field-masked snapshot RPCs) to skip the
    # refetch round-trip. This is a superset of what clients consume today --
    # no second event format, no migration. Do NOT build a translation layer
    # off this field as part of F1.
    payload: dict[str, Any] | None = None


class PresenceEvent(_BaseEvent):
    """Broadcast when the supervisor's debounced presence state transitions.

    Asymmetric debounce: connect→attended is immediate (responsive ramp);
    disconnect→headless is delayed by PRESENCE_DISCONNECT_DEBOUNCE_S to
    absorb blips.

    ``version`` is monotonic per supervisor lifetime. Subscribers MUST
    ignore events with version not strictly greater than the last seen,
    so that bridges that don't preserve order can't corrupt downstream
    workers' adaptive cadence.
    """

    type: Literal["presence"] = "presence"
    state: PresenceState
    user_count: int = 0
    kinds: dict[str, int] = Field(default_factory=dict)
    version: int


class SchedulerModeEvent(_BaseEvent):
    """Broadcast when the scheduler mode changes."""

    type: Literal["scheduler.mode"] = "scheduler.mode"
    from_mode: str
    to_mode: str
    changed_by: Literal["user", "api"] = "user"


class SchedulerDecisionEvent(_BaseEvent):
    """Emitted on every crow_magic tick per (harness, window_key)."""

    type: Literal["scheduler.decision"] = "scheduler.decision"
    mode: str
    harness: str
    window_key: str
    decision: bool
    usage: float
    t_until_reset: float
    t_period: float
    threshold: float
    rationale: str
    kicked_ticket_id: str | None = None


class UsageResetEvent(_BaseEvent):
    """Emitted when consecutive snapshots show a usage reset (drop > 80% of prev)."""

    type: Literal["usage.reset"] = "usage.reset"
    harness: str
    prev_pct: float
    curr_pct: float


class ConversationBlockEvent(_BaseEvent):
    """Content-bearing conversation block push event.

    Additive event kind for event-sourced transcripts. ``action`` distinguishes
    immutable appends from live trailing-block updates without touching the
    closed ``Entity`` enum or key-only ``StateSnapshotEvent`` contract.
    """

    type: Literal["conversation.block"] = "conversation.block"
    conversation_id: str
    action: Literal["block-appended", "block-updated"]
    block: dict[str, Any]


class ConversationStateEvent(_BaseEvent):
    """Content-bearing conversation liveness push event.

    Companion to ``ConversationBlockEvent`` on the same additive-event-kind
    seam: emitted whenever a conversation's parsed harness UI state
    (``working`` / ``awaiting_input`` / ``awaiting_approval``) or its queued
    -but-undelivered user message changes. Key-only ``StateSnapshotEvent``
    semantics do not fit here: the TUI renders the queued line / awaiting
    badge live and there is no snapshot entity for conversations.
    """

    type: Literal["conversation.state"] = "conversation.state"
    conversation_id: str
    live_state: str | None = None
    queued_message: str | None = None


class TmuxFrameEvent(_BaseEvent):
    """Raw ANSI frame from tmux for the focused pane.

    Streamed **only** while the tmux fullscreen mode is active (``ctrl+y``
    in the Ink TUI); the bus subscription is opened on enter and closed on
    exit. There is **no standing cost** when nobody is subscribed — the
    capture loop runs only for the lifetime of the subscription task.

    The ``frame`` field carries the full rendered ANSI output of the pane
    as a single snapshot string (from ``tmux capture-pane -e``). The
    consumer replaces its display on every event (no incremental patching).

    Pane-scoping note: the current ``EventFilter`` has no ``pane_id``
    field.  The server delivers a single stream for the configured focused
    pane; per-pane multiplexing is deferred (flagged in ``protocol.ts``
    C14 note).
    """

    type: Literal["tmux.frame"] = "tmux.frame"
    frame: str


# --- Discriminated union of inner events ------------------------------------

BusEvent = Annotated[
    HeartbeatEvent
    | SummaryEvent
    | QuestionEvent
    | NoteEvent
    | EscalationEvent
    | StatusChangeEvent
    | ErrorEvent
    | CommandEvent
    | StateSnapshotEvent
    | PresenceEvent
    | SchedulerModeEvent
    | SchedulerDecisionEvent
    | UsageResetEvent
    | ConversationBlockEvent
    | ConversationStateEvent
    | TmuxFrameEvent,
    Field(discriminator="type"),
]

BUS_EVENT_ADAPTER: TypeAdapter[BusEvent] = TypeAdapter(BusEvent)


# Back-compat: the legacy ``AgentEvent`` excludes the three new types so
# call sites that pattern-match on it keep their narrowed semantics until
# they migrate to ``BusEvent``.
AgentEvent = Annotated[
    HeartbeatEvent
    | SummaryEvent
    | QuestionEvent
    | EscalationEvent
    | StatusChangeEvent
    | ErrorEvent,
    Field(discriminator="type"),
]


# === Filter ==================================================================


class EventFilter(BaseModel):
    """Server-applied filter. Fields compose with AND; ``None`` matches any.

    The broker MUST apply filters before fanout. At the future tier-3 scale
    (~45 clients in worker_bus_refactor handoff §5.2) client-side filtering
    is O(clients × events) and untenable.
    """

    role: Role | None = None
    ticket_id: str | None = None
    type: str | None = None
    entity: Entity | None = None
    target_worker: str | None = None
    kind: str | None = None
    agent_id: str | None = None

    def matches(self, event: BaseModel) -> bool:
        if self.role is not None and getattr(event, "role", None) != self.role:
            return False
        if self.agent_id is not None and getattr(event, "agent_id", None) != self.agent_id:
            return False
        if self.ticket_id is not None and getattr(event, "ticket_id", None) != self.ticket_id:
            return False
        if self.type is not None and getattr(event, "type", None) != self.type:
            return False
        if self.entity is not None and getattr(event, "entity", None) != self.entity:
            return False
        if (
            self.target_worker is not None
            and getattr(event, "target_worker", None) != self.target_worker
        ):
            return False
        if self.kind is not None and getattr(event, "kind", None) != self.kind:
            return False
        return True


# === Envelope bodies =========================================================


class HelloBody(BaseModel):
    """Sent by the client as the first message after socket connect.

    Server responds with ``AckBody(kind="subscribed")`` after a successful
    handshake or ``ErrBody(code="protocol_version_mismatch")`` if versions
    disagree. ``client_id`` is stable across reconnects so the supervisor
    can resume in-flight RPC state and presence counting correctly.
    """

    protocol_version: int
    client_kind: ClientKind
    client_id: str
    since_id: int | None = None


class SubArgs(BaseModel):
    """Subscribe arguments. ``since_id=N`` MUST replay every persisted event
    with ``events.id > N`` before the live tail begins, terminated by
    ``AckBody(kind="replay_done", watermark=...)``.

    ``presence_retain=True`` makes presence sticky-retain: server emits the
    current PresenceEvent on the new subscription immediately after
    replay_done so late subscribers aren't blind to current state.
    """

    filter: EventFilter = Field(default_factory=EventFilter)
    since_id: int | None = None
    presence_retain: bool = True


class RpcArgs(BaseModel):
    target: str
    body: dict[str, Any] = Field(default_factory=dict)
    timeout_s: float = 30.0


class AckBody(BaseModel):
    kind: Literal["subscribed", "replay_done", "rpc_reply", "pong"]
    watermark: int | None = None
    result: dict[str, Any] | None = None


class ErrBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class WakeBody(BaseModel):
    """Per-client signal sent immediately on connect / reconnect.

    Distinct from PresenceEvent. PresenceEvent is broadcast and represents
    a global debounced transition; WakeEvent is per-client and represents
    "you just joined — here are the entities whose state is most likely
    stale for you." Not persisted to ``events``.
    """

    client_id: str
    reason: Literal["connect", "reconnect"] = "connect"
    fresh_state_hints: list[Entity] = Field(default_factory=list)


# === Wire envelope (discriminated by `op`) ===================================


class _BaseMessage(BaseModel):
    schema_version: int = PROTOCOL_VERSION
    correlation_id: str = ""


class HelloMessage(_BaseMessage):
    op: Literal["hello"] = "hello"
    body: HelloBody


class PubMessage(_BaseMessage):
    op: Literal["pub"] = "pub"
    event: BusEvent


class SubMessage(_BaseMessage):
    op: Literal["sub"] = "sub"
    args: SubArgs


class RpcMessage(_BaseMessage):
    op: Literal["rpc"] = "rpc"
    args: RpcArgs


class AckMessage(_BaseMessage):
    op: Literal["ack"] = "ack"
    body: AckBody


class ErrMessage(_BaseMessage):
    op: Literal["err"] = "err"
    body: ErrBody


class WakeMessage(_BaseMessage):
    op: Literal["wake"] = "wake"
    body: WakeBody


WireMessage = Annotated[
    HelloMessage | PubMessage | SubMessage | RpcMessage | AckMessage | ErrMessage | WakeMessage,
    Field(discriminator="op"),
]

WIRE_MESSAGE_ADAPTER: TypeAdapter[WireMessage] = TypeAdapter(WireMessage)


# === Wire constants ==========================================================

SOCKET_RUNTIME_SUBDIR = "murder"
SOCKET_BASENAME = "bus.sock"

DEFAULT_RPC_TIMEOUT_S = 30.0
DEFAULT_HEARTBEAT_INTERVAL_S = 5.0
DEFAULT_LEASE_TTL_S = 30.0
DEFAULT_MAX_COMMAND_ATTEMPTS = 3
COMMAND_REAPER_INTERVAL_S = 5.0

PRESENCE_DISCONNECT_DEBOUNCE_S = 30.0
PRESENCE_USER_KINDS: frozenset[ClientKind] = frozenset({ClientKind.TUI, ClientKind.WEB})
# Only these client kinds count toward PresenceEvent.user_count.

SUBSCRIBER_QUEUE_DEFAULT = 1024
IDEMPOTENCY_WINDOW_S = 60.0


__all__ = [
    "PROTOCOL_VERSION",
    "Role",
    "TicketStatus",
    "AgentStatus",
    "CommandStatus",
    "Entity",
    "PresenceState",
    "ClientKind",
    "HeartbeatEvent",
    "SummaryEvent",
    "QuestionEvent",
    "NoteEvent",
    "EscalationEvent",
    "StatusChangeEvent",
    "ErrorEvent",
    "CommandEvent",
    "StateSnapshotEvent",
    "PresenceEvent",
    "SchedulerModeEvent",
    "SchedulerDecisionEvent",
    "UsageResetEvent",
    "ConversationBlockEvent",
    "ConversationStateEvent",
    "TmuxFrameEvent",
    "BusEvent",
    "AgentEvent",
    "BUS_EVENT_ADAPTER",
    "EventFilter",
    "HelloBody",
    "SubArgs",
    "RpcArgs",
    "AckBody",
    "ErrBody",
    "WakeBody",
    "HelloMessage",
    "PubMessage",
    "SubMessage",
    "RpcMessage",
    "AckMessage",
    "ErrMessage",
    "WakeMessage",
    "WireMessage",
    "WIRE_MESSAGE_ADAPTER",
    "SOCKET_RUNTIME_SUBDIR",
    "SOCKET_BASENAME",
    "DEFAULT_RPC_TIMEOUT_S",
    "DEFAULT_HEARTBEAT_INTERVAL_S",
    "DEFAULT_LEASE_TTL_S",
    "DEFAULT_MAX_COMMAND_ATTEMPTS",
    "COMMAND_REAPER_INTERVAL_S",
    "PRESENCE_DISCONNECT_DEBOUNCE_S",
    "PRESENCE_USER_KINDS",
    "SUBSCRIBER_QUEUE_DEFAULT",
    "IDEMPOTENCY_WINDOW_S",
]
