"""Private in-process orchestration notifications.

Application clients use the typed WebSocket contract in
``murder.app.protocol``. This module contains only notification and command
shapes exchanged inside one service process; it is not a wire protocol and
must not grow client-facing envelopes.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, ClassVar, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from murder.runtime.agents.types import AgentRole
from murder.runtime.orchestration.commands import OrchestrationCommand
from murder.runtime.orchestration.worker_names import WorkerName

# === Closed enums ============================================================


class _StringEnum(str, Enum):
    """Python-3.10-compatible string enum."""

    def __str__(self) -> str:
        return str.__str__(self)


class CommandStatus(_StringEnum):
    PENDING = "pending"
    IN_FLIGHT = "in_flight"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"



# === Internal notifications ==================================================


class _BaseEvent(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    ts: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    run_id: str
    agent_id: str = ""
    role: AgentRole | None = None
    ticket_id: str | None = None

    # Flight-recorder routing. The recorder subscribes to the private notifier
    # and captures each event into the table named here — events self-describe
    # their family, so there is no central match/registry to edit per new type.
    # Default ``event_records`` means "captured into the generic bulky dump";
    # subclasses whose forensic shape belongs in a typed family override it; a
    # value of ``None`` opts the event out of capture entirely. A structural
    # guard test asserts every subclass declares a value that is None or a real
    # family, so a typo'd family fails loudly at CI instead of silently never
    # being captured. (ClassVar → pydantic treats it as a class attr, not a
    # field, so it is not serialized and does not touch the wire contract.)
    record_family: ClassVar[str | None] = "event_records"


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


# --- Worker wakeups and coordination decisions -------------------------------


class CommandEvent(_BaseEvent):
    """A durable request to a closed internal worker target.

    The command repository is the work queue and audit source; this in-process
    notification only wakes current-process consumers.
    """

    type: Literal["command"] = "command"
    record_family: ClassVar[str] = "command_records"
    target_worker: WorkerName
    kind: OrchestrationCommand
    payload: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str
    idempotency_key: str
    status: CommandStatus = CommandStatus.PENDING
    claimed_by: str | None = None
    lease_expires_at: int | None = None
    attempt_count: int = 0
    retryable: bool = True
    result: dict[str, Any] | None = None


class SchedulerModeEvent(_BaseEvent):
    """Broadcast when the scheduler mode changes."""

    type: Literal["scheduler.mode"] = "scheduler.mode"
    from_mode: str
    to_mode: str
    changed_by: Literal["user", "api"] = "user"


class SchedulerDecisionEvent(_BaseEvent):
    """Emitted on every crow_magic tick per (harness, window_key)."""

    type: Literal["scheduler.decision"] = "scheduler.decision"
    record_family: ClassVar[str] = "decision_records"
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


class CompletionVerdictEvent(_BaseEvent):
    """A completion coordinator verdict for a ticket (peer of SchedulerDecisionEvent).

    Published at both verdict sites in ``verdict/completion/coordinator.py`` so
    the forensic capture rides the one in-process notification path instead of
    a parallel ``record_decision()`` call.
    """

    type: Literal["completion.verdict"] = "completion.verdict"
    record_family: ClassVar[str] = "decision_records"
    completed: bool
    ticket_failed: bool = False
    failed_checks: list[str] = Field(default_factory=list)


class AgentLifecycleEvent(_BaseEvent):
    """A rich agent-registry mutation (register / rename / clear / force_stop).

    Published at registry mutations — INCLUDING the ones that emitted nothing
    before (``clear``, force-stop) — so the recorder captures them into
    ``agent_records`` and the per-emitter ``record_agent()`` calls go away.
    """

    type: Literal["agent.lifecycle"] = "agent.lifecycle"
    record_family: ClassVar[str] = "agent_records"
    op: Literal["register", "rename", "clear", "force_stop"]
    details: dict[str, Any] = Field(default_factory=dict)
    reason: str | None = None


class UsageResetEvent(_BaseEvent):
    """Emitted when consecutive snapshots show a usage reset (drop > 80% of prev)."""

    type: Literal["usage.reset"] = "usage.reset"
    harness: str
    prev_pct: float
    curr_pct: float


class ConversationBlockEvent(_BaseEvent):
    """Content-bearing conversation block push event.

    Additive event kind for event-sourced transcripts. ``action`` distinguishes
    immutable appends from live trailing-block updates without relying on a
    generic entity-invalidated notification.

    ``chunk-summarized`` is an incremental hint that a rolling chunk summary
    was persisted. For that action ``block`` carries
    ``{conversation_id, summary, block_ids}``, not a transcript-row block.
    """

    type: Literal["conversation.block"] = "conversation.block"
    conversation_id: str
    action: Literal["block-appended", "block-updated", "chunk-summarized"]
    block: dict[str, Any]


class ConversationStateEvent(_BaseEvent):
    """Content-bearing conversation liveness push event.

    Companion to ``ConversationBlockEvent`` on the same additive-event-kind
    seam: emitted whenever a conversation's parsed harness UI state
    (``working`` / ``awaiting_input`` / ``awaiting_approval``) or its queued
    -but-undelivered user message changes. The application renders the queued
    line and awaiting badge from its typed conversation projection.
    """

    type: Literal["conversation.state"] = "conversation.state"
    conversation_id: str
    live_state: str | None = None
    queued_message: str | None = None


OrchestrationEvent = (
    HeartbeatEvent
    | SummaryEvent
    | EscalationEvent
    | StatusChangeEvent
    | ErrorEvent
    | CommandEvent
    | SchedulerModeEvent
    | SchedulerDecisionEvent
    | CompletionVerdictEvent
    | AgentLifecycleEvent
    | UsageResetEvent
    | ConversationBlockEvent
    | ConversationStateEvent
)


# === Internal orchestration constants ========================================

DEFAULT_LEASE_TTL_S = 30.0
DEFAULT_MAX_COMMAND_ATTEMPTS = 3
COMMAND_REAPER_INTERVAL_S = 5.0


__all__ = [
    "CommandStatus",
    "HeartbeatEvent",
    "SummaryEvent",
    "EscalationEvent",
    "StatusChangeEvent",
    "ErrorEvent",
    "CommandEvent",
    "SchedulerModeEvent",
    "SchedulerDecisionEvent",
    "CompletionVerdictEvent",
    "AgentLifecycleEvent",
    "UsageResetEvent",
    "ConversationBlockEvent",
    "ConversationStateEvent",
    "OrchestrationEvent",
    "DEFAULT_LEASE_TTL_S",
    "DEFAULT_MAX_COMMAND_ATTEMPTS",
    "COMMAND_REAPER_INTERVAL_S",
]
