"""Typed service-client contract and snapshot DTOs (W1)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import ClassVar, Protocol

from murder.tickets.status import TicketStatus


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
    wave: int
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
    plan_md: str
    working_notes_md: str
    checklist: tuple[ChecklistItem, ...]
    as_of: datetime
    invalidation_key: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "checklist", tuple(self.checklist))


@dataclass(frozen=True, slots=True)
class CrowSessionSummary:
    agent_id: str
    role: str
    ticket_id: str | None
    ticket_title: str
    status: str
    session_name: str | None
    harness: str | None
    last_seen: datetime | None
    started_at: datetime | None
    ticket_status: str | None
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
class SchedulerDecisionSummary:
    harness: str
    decision: int
    rationale: str
    kicked_ticket_id: str | None


@dataclass(frozen=True, slots=True)
class ScheduleTicketRow:
    id: str
    title: str
    wave: int
    status: str
    schedule_at: str | None
    harness: str | None
    model: str | None
    metadata_sync_state: str
    metadata_parse_error: str | None
    metadata_conflict_reason: str | None
    deps_ok: bool


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
class TicketRef:
    id: str
    title: str


@dataclass(frozen=True, slots=True)
class TicketCarveSnapshot:
    ticket_id: str
    fields: Mapping[str, object]
    wave_options: tuple[int, ...]
    dependency_options: tuple[TicketRef, ...]
    known_skills: tuple[str, ...]

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


class InvalidationKeys:
    dispatch: ClassVar[str] = "dispatch"
    schedule: ClassVar[str] = "schedule"
    crows: ClassVar[str] = "crows"
    escalations: ClassVar[str] = "escalations"
    plans: ClassVar[str] = "plans"
    notes: ClassVar[str] = "notes"
    settings: ClassVar[str] = "settings"

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

    async def ack_escalation(self, escalation_id: int) -> None: ...

    async def send_agent_message(
        self,
        agent_id: str,
        message: str,
        *,
        ticket_id: str | None = None,
    ) -> CommandResult: ...


__all__ = [
    "ChecklistItem",
    "CommandRequest",
    "CommandResult",
    "CommandStatus",
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
]
