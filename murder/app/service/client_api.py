"""Typed service-client contract and snapshot DTOs (W1)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import datetime
from enum import Enum
from types import MappingProxyType, UnionType
from typing import Any, ClassVar, Protocol, Union, get_args, get_origin, get_type_hints

from murder.bus.protocol import BusEvent
from murder.work.tickets.status import TicketStatus


class CommandStatus(str, Enum):
    QUEUED = "queued"
    ACCEPTED = "accepted"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class CommandRequest:
    command_type: str
    payload: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))
    correlation_id: str | None = None
    idempotency_key: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))


@dataclass(frozen=True, slots=True)
class CommandResult:
    command_id: str
    status: CommandStatus
    error: str | None = None
    result: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        if self.result is not None:
            object.__setattr__(self, "result", MappingProxyType(dict(self.result)))


@dataclass(frozen=True, slots=True)
class SettingsChangeRequest:
    changes: dict[str, object]
    scope: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "changes", dict(self.changes))


@dataclass(frozen=True, slots=True)
class CurrentSettingsSnapshot:
    settings: dict[str, object]
    as_of: datetime
    invalidation_key: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "settings", dict(self.settings))


@dataclass(frozen=True, slots=True)
class TicketSummary:
    id: str
    title: str
    status: TicketStatus
    harness: str | None
    model: str | None


@dataclass(frozen=True, slots=True)
class DispatchSnapshot:
    tickets: tuple[TicketSummary, ...]
    as_of: datetime
    invalidation_key: str


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
    # Legacy split sections retained for backward compatibility with existing consumers
    # (the Textual client / older tests). Ink reads `body`; these mirror the same prose.
    plan_md: str
    working_notes_md: str
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
    session_name: str | None
    harness: str | None
    last_seen: datetime | None
    started_at: datetime | None
    ticket_status: str | None
    worktree_path: str | None = None
    model: str | None = None
    open_escalations: int = 0
    max_severity: int = 0


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
    pending_dep_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "pending_dep_ids", tuple(self.pending_dep_ids))


@dataclass(frozen=True, slots=True)
class UsageGaugeSummary:
    harness: str
    window_key: str
    pct: float
    t_until_reset_minutes: float
    t_period_minutes: float = 0.0


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
class TicketRef:
    id: str
    title: str


@dataclass(frozen=True, slots=True)
class TicketCarveSnapshot:
    ticket_id: str
    fields: Mapping[str, object]
    dependency_options: tuple[TicketRef, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "fields", dict(self.fields))


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
class ConversationSummary:
    conversation_id: str
    agent_id: str
    harness: str | None
    model: str | None
    harness_session_id: str | None
    live_state: str | None
    condensed: str | None
    status: str
    blocks: tuple[ConversationBlockSummary, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "blocks", tuple(self.blocks))


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

    @staticmethod
    def ticket_detail(ticket_id: str) -> str:
        return f"ticket_detail:{ticket_id}"


class MurderServiceClient(Protocol):
    """Application boundary for TUI and future gateways."""

    async def submit_command(self, request: CommandRequest) -> CommandResult: ...

    async def get_dispatch_snapshot(self) -> DispatchSnapshot: ...

    async def get_schedule_snapshot(self) -> ScheduleSnapshot: ...

    async def get_ticket_detail(self, ticket_id: str) -> TicketDetailSnapshot | None: ...

    async def get_crow_snapshot(self) -> CrowSnapshot: ...

    async def get_escalations(self) -> EscalationsSnapshot: ...

    async def get_plans_snapshot(self) -> PlansSnapshot: ...

    async def get_notes_snapshot(self) -> NotesSnapshot: ...

    async def get_reports_snapshot(self) -> ReportsSnapshot: ...

    async def get_conversations_snapshot(self) -> ConversationsSnapshot: ...

    def subscribe_conversation_blocks(self) -> AsyncIterator[BusEvent]: ...

    async def get_plan_display(self, name: str) -> PlanDisplaySnapshot | None: ...

    async def get_note_display(self, name: str) -> NoteDisplaySnapshot | None: ...

    async def get_report_display(self, name: str) -> ReportDisplaySnapshot | None: ...

    async def ack_escalation(self, escalation_id: int) -> None: ...

    async def send_agent_message(
        self,
        agent_id: str,
        message: str,
        *,
        ticket_id: str | None = None,
    ) -> CommandResult: ...

    async def spawn_rogue(
        self,
        harness: str,
        model: str,
        effort: str | None = None,
        name: str | None = None,
        *,
        worktree_path: str | None = None,
        worktree_branch: str | None = None,
    ) -> str: ...


def dto_to_wire(value: Any) -> Any:
    """Convert service DTOs into JSON-compatible RPC payload values."""
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


def dto_from_wire(cls: type[Any], value: Any) -> Any:
    """Rehydrate service DTOs from JSON-compatible RPC payload values."""
    origin = get_origin(cls)
    args = get_args(cls)
    if origin in (Union, UnionType):
        non_none = [arg for arg in args if arg is not type(None)]
        if value is None:
            return None
        if len(non_none) == 1:
            return dto_from_wire(non_none[0], value)
    if origin is tuple:
        item_type = args[0] if args else Any
        return tuple(dto_from_wire(item_type, item) for item in (value or ()))
    if origin in (dict, Mapping):
        return dict(value or {})
    if cls is Any or cls is object:
        return value
    if cls is datetime:
        return datetime.fromisoformat(str(value))
    if isinstance(cls, type) and issubclass(cls, Enum):
        return cls(value)
    if isinstance(cls, type) and is_dataclass(cls):
        hints = get_type_hints(cls)
        return cls(
            **{
                field.name: dto_from_wire(hints.get(field.name, Any), value[field.name])
                for field in fields(cls)
                if field.name in value
            }
        )
    return value


__all__ = [
    "ChecklistItem",
    "CommandRequest",
    "CommandResult",
    "CommandStatus",
    "ConversationBlockSummary",
    "ConversationSummary",
    "ConversationsSnapshot",
    "CrowSessionSummary",
    "CrowSnapshot",
    "CurrentSettingsSnapshot",
    "DispatchSnapshot",
    "EscalationSummary",
    "EscalationsSnapshot",
    "InvalidationKeys",
    "MurderServiceClient",
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
    "SettingsChangeRequest",
    "UsageGaugeSummary",
    "UsageGaugeDrillInSnapshot",
    "UsageResetEvent",
    "UsageBurnRow",
    "PlanDisplaySnapshot",
    "NoteDisplaySnapshot",
    "TicketRef",
    "TicketCarveSnapshot",
    "TicketDetailSnapshot",
    "TicketSummary",
    "dto_from_wire",
    "dto_to_wire",
]
