"""Read-model DTOs returned by the public application protocol."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import datetime
from enum import Enum
from typing import Any, ClassVar

from murder.work.tickets.status import TicketStatus


@dataclass(frozen=True, slots=True)
class TicketSummary:
    id: str
    title: str
    status: TicketStatus
    harness: str | None
    model: str | None
    # Parent ticket id for subticket linkage; None for a top-level ticket.
    # Sourced from the tickets.parent_ticket_id column (analogous to PlanSummary.parent,
    # which derives from frontmatter — tickets store the parent as a real column).
    parent: str | None = None


@dataclass(frozen=True, slots=True)
class ChecklistItem:
    text: str
    done: bool


@dataclass(frozen=True, slots=True)
class TicketDetailSnapshot:
    id: str
    title: str
    status: TicketStatus
    # Unified frontmatter-stripped markdown body the C8 editor renders and edits.
    # Carries the `# Checklist` `[ ]`/`[x]` lines the editor toggles (newui-inktui C8,
    # lines 164-167: "Ticket = frontmatter + body"; "Checklist rides in the body").
    body: str
    checklist: tuple[ChecklistItem, ...]
    # Display-only frontmatter header fields the C8 editor shows above the body
    # (line 244: harness+model are display-only; deps/worktree round out the header).
    # Sourced from the ticket record (frontmatter persisted in the tickets table /
    # ticket_deps). `worktree`/`harness`/`model` are nullable when the ticket omits them.
    deps: tuple[str, ...]
    harness: str | None
    model: str | None
    worktree: str | None
    # Runtime state delivered alongside the doc (DB-only per line 165). The editor shows
    # status; schedule_at backs the free-form schedule input (line 245).
    schedule_at: str | None
    as_of: datetime
    invalidation_key: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "checklist", tuple(self.checklist))
        object.__setattr__(self, "deps", tuple(self.deps))


@dataclass(frozen=True, slots=True)
class CrowSessionSummary:
    agent_id: str
    role: str
    ticket_id: str | None
    ticket_title: str | None
    status: str
    display_name: str | None
    harness: str | None
    last_seen: datetime | None
    started_at: datetime | None
    ticket_status: str | None
    worktree_path: str | None = None
    model: str | None = None
    open_escalations: int = 0
    max_severity: int = 0
    # Canonical durable HarnessSessionRecord identity. Agent id is the fallback
    # identity for sessions which predate durable harness-session records.
    session_id: str | None = None


@dataclass(frozen=True, slots=True)
class CrowSnapshot:
    sessions: tuple[CrowSessionSummary, ...]
    as_of: datetime
    invalidation_key: str


@dataclass(frozen=True, slots=True)
class EscalationSummary:
    id: int
    ticket_id: str | None
    severity: int
    reason: str
    to_recipient: str
    body_path: str | None
    ticket_status: str | None = None


@dataclass(frozen=True, slots=True)
class EscalationsSnapshot:
    active: tuple[EscalationSummary, ...]
    as_of: datetime
    invalidation_key: str
    history: tuple[EscalationSummary, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class PlanSummary:
    name: str
    status: str
    revision_count: int
    sync_state: str
    # C11 plans panel render inputs (newui-inktui line 169 + C11 row 423):
    #  - `parent`: parent plan's NAME (matched against another row) for 4-space
    #    indentation; null/absent for a top-level plan. Sourced from the plan's
    #    frontmatter `parent` key (the only non-derived parent metadata the store
    #    holds); null when unset.
    #  - `updated_at`: recency timestamp driving the effective-recency ordering.
    #  - `char_count`: plan body size shown in the row.
    parent: str | None = None
    updated_at: datetime | None = None
    char_count: int = 0


@dataclass(frozen=True, slots=True)
class PlansSnapshot:
    plans: tuple[PlanSummary, ...]
    as_of: datetime
    invalidation_key: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "plans", tuple(self.plans))


@dataclass(frozen=True, slots=True)
class NoteSummary:
    name: str
    char_count: int
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class NotesSnapshot:
    notes: tuple[NoteSummary, ...]
    as_of: datetime
    invalidation_key: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "notes", tuple(self.notes))


@dataclass(frozen=True, slots=True)
class ReportSummary:
    name: str
    char_count: int
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ReportsSnapshot:
    reports: tuple[ReportSummary, ...]
    as_of: datetime
    invalidation_key: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "reports", tuple(self.reports))


@dataclass(frozen=True, slots=True)
class HistoryItemSummary:
    """One user-message intention as the history view cares about it.

    Derived (not stored) from the durable ``conversation_blocks kind='user'``
    spine joined against the ``history_status`` overlay. ``status`` is the
    zero-LLM v0 taxonomy (``open`` / ``stale`` / ``dismissed``). The
    resumability triple (``harness`` / ``conversation_status`` / ``resumable``)
    is carried from day one so the /resume keybind is additive on this panel.
    ``conversation_id`` is the resume key (a conversation UUID), distinct from
    ``target`` (the agent_id).
    """

    item_id: str
    conversation_id: str
    text: str
    target: str
    ts: str
    status: str
    harness: str | None
    conversation_status: str
    resumable: bool


@dataclass(frozen=True, slots=True)
class HistorySnapshot:
    items: tuple[HistoryItemSummary, ...]
    as_of: datetime
    invalidation_key: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", tuple(self.items))


@dataclass(frozen=True, slots=True)
class SchedulerDecisionSummary:
    harness: str
    decision: int
    rationale: str
    kicked_ticket_id: str | None


@dataclass(frozen=True, slots=True)
class ScheduleTicketRow:
    id: str
    title: str
    status: str
    last_update_at: datetime
    last_update_label: str
    schedule_at: str | None
    harness: str | None
    model: str | None
    metadata_sync_state: str
    metadata_parse_error: str | None
    metadata_conflict_reason: str | None
    # Dep ids that are NOT yet satisfied (status not in done/archived) — the
    # "waiting-on-dependency" glyph signal. Empty tuple = all deps satisfied / no deps.
    pending_dep_ids: tuple[str, ...]
    # Parent ticket id for the subtree/subticket glyph; None for a top-level ticket.
    parent: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "pending_dep_ids", tuple(self.pending_dep_ids))


@dataclass(frozen=True, slots=True)
class UsageGaugeSummary:
    harness: str
    window_key: str
    pct: float
    t_until_reset_minutes: float
    t_period_minutes: float = 0.0
    steering: str = "auto"
    # ISO-8601 UTC timestamp of the latest harness_usage_snapshots row for this harness.
    fetched_at: str | None = None


@dataclass(frozen=True, slots=True)
class UsageResetEvent:
    reset_at: str
    peak_pct: float


@dataclass(frozen=True, slots=True)
class UsageBurnRow:
    ticket_id: str
    title: str
    active_minutes: int


@dataclass(frozen=True, slots=True)
class UsageGaugeDrillInSnapshot:
    harness: str
    window_key: str
    sparkline: str
    recent_resets: tuple[UsageResetEvent, ...]
    burn_rows: tuple[UsageBurnRow, ...]


@dataclass(frozen=True, slots=True)
class PlanDisplaySnapshot:
    name: str
    markdown: str


@dataclass(frozen=True, slots=True)
class NoteDisplaySnapshot:
    name: str
    markdown: str


@dataclass(frozen=True, slots=True)
class ReportDisplaySnapshot:
    name: str
    markdown: str


@dataclass(frozen=True, slots=True)
class CalendarRunningAgent:
    agent_id: str
    ticket_id: str
    started_at: str
    harness: str | None


@dataclass(frozen=True, slots=True)
class CalendarScheduledTicket:
    ticket_id: str
    schedule_at: str
    harness: str | None


@dataclass(frozen=True, slots=True)
class ScheduleSnapshot:
    scheduler_mode: str
    mode_rationale: str
    active_tickets: tuple[ScheduleTicketRow, ...]
    recent_done_tickets: tuple[ScheduleTicketRow, ...]
    archived_tickets: tuple[ScheduleTicketRow, ...]
    scheduler_decisions: tuple[SchedulerDecisionSummary, ...]
    usage_gauges: tuple[UsageGaugeSummary, ...]
    calendar_harnesses: tuple[str, ...]
    running_agents: tuple[CalendarRunningAgent, ...]
    scheduled_tickets: tuple[CalendarScheduledTicket, ...]
    as_of: datetime
    invalidation_key: str


@dataclass(frozen=True, slots=True)
class ConversationBlockSummary:
    id: int | None
    conversation_id: str
    ordinal: int
    kind: str
    payload: Mapping[str, object]
    sealed: bool
    service_received_at: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", dict(self.payload))


@dataclass(frozen=True, slots=True)
class ConversationChunkSummary:
    """One Condensed-view chunk summary with its attributed source block ids.

    ``block_ids`` are explicit pointers into the conversation's blocks (the
    attribution contract) so the Condensed view can replace exactly those blocks
    with ``summary`` and later reveal/jump back to the source.
    """

    summary_id: int
    chunk_idx: int
    summary: str
    block_ids: tuple[int, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "block_ids", tuple(self.block_ids))


@dataclass(frozen=True, slots=True)
class ConversationSummary:
    conversation_id: str
    agent_id: str
    harness: str | None
    model: str | None
    harness_session_id: str | None
    live_state: str | None
    # Ordered rolling chunk summaries (Condensed view). Replaces the old single
    # `condensed` scalar — that column was dropped (TUIchat Phase 4). Empty when
    # no chunk has been summarized yet (the view falls back to Verbose).
    chunk_summaries: tuple[ConversationChunkSummary, ...]
    queued_message: str | None
    status: str
    blocks: tuple[ConversationBlockSummary, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "blocks", tuple(self.blocks))
        object.__setattr__(self, "chunk_summaries", tuple(self.chunk_summaries))


@dataclass(frozen=True, slots=True)
class ConversationsSnapshot:
    conversations: tuple[ConversationSummary, ...]
    as_of: datetime
    invalidation_key: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "conversations", tuple(self.conversations))


class InvalidationKeys:
    dispatch: ClassVar[str] = "dispatch"
    schedule: ClassVar[str] = "schedule"
    crows: ClassVar[str] = "crows"
    escalations: ClassVar[str] = "escalations"
    plans: ClassVar[str] = "plans"
    notes: ClassVar[str] = "notes"
    reports: ClassVar[str] = "reports"
    settings: ClassVar[str] = "settings"
    conversations: ClassVar[str] = "conversations"
    history: ClassVar[str] = "history"

    @staticmethod
    def ticket_detail(ticket_id: str) -> str:
        return f"ticket_detail:{ticket_id}"


def dto_to_wire(value: Any) -> Any:
    """Convert protocol read models into JSON-compatible values."""
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: dto_to_wire(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): dto_to_wire(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [dto_to_wire(item) for item in value]
    return value
__all__ = [
    "ChecklistItem",
    "ConversationBlockSummary",
    "ConversationChunkSummary",
    "ConversationSummary",
    "ConversationsSnapshot",
    "CrowSessionSummary",
    "CrowSnapshot",
    "EscalationSummary",
    "EscalationsSnapshot",
    "InvalidationKeys",
    "NoteSummary",
    "NotesSnapshot",
    "ReportDisplaySnapshot",
    "ReportSummary",
    "ReportsSnapshot",
    "PlanSummary",
    "PlansSnapshot",
    "CalendarRunningAgent",
    "CalendarScheduledTicket",
    "ScheduleSnapshot",
    "ScheduleTicketRow",
    "SchedulerDecisionSummary",
    "UsageGaugeSummary",
    "UsageGaugeDrillInSnapshot",
    "UsageResetEvent",
    "UsageBurnRow",
    "PlanDisplaySnapshot",
    "NoteDisplaySnapshot",
    "TicketDetailSnapshot",
    "TicketSummary",
    "dto_to_wire",
]
