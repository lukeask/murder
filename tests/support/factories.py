from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from murder.app.service.client_api import (
    ConversationBlockSummary,
    ConversationSummary,
    ConversationsSnapshot,
    CrowSessionSummary,
    CrowSnapshot,
    DispatchSnapshot,
    EscalationSummary,
    EscalationsSnapshot,
    NoteSummary,
    NotesSnapshot,
    PlanSummary,
    PlansSnapshot,
    ReportSummary,
    ReportsSnapshot,
    ScheduleSnapshot,
    ScheduleTicketRow,
    TicketSummary,
)
from murder.app.tui.crow_health import Health
from murder.app.tui.stores.roster import CrowEntry

# Shared default timestamp so factory-built snapshots compare equal across calls.
FACTORY_DT = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def make_repo_root(tmp_path: Path, name: str = "repo") -> Path:
    """Create a disposable repo root for tests that exercise .murder state."""
    root = tmp_path / name
    root.mkdir(parents=True, exist_ok=True)
    return root


# ---------------------------------------------------------------------------
# Roster / crow domain
# ---------------------------------------------------------------------------


def factory_crow_session(**kwargs: Any) -> CrowSessionSummary:
    """Build a CrowSessionSummary; defaults model a running crow."""
    defaults: dict[str, Any] = dict(
        agent_id="crow-t001",
        role="crow",
        ticket_id="t001",
        ticket_title="Fix thing",
        status="running",
        session_name="murder_demo_crow_t001",
        harness="cursor",
        last_seen=None,
        started_at=None,
        ticket_status="in_progress",
    )
    defaults.update(kwargs)
    return CrowSessionSummary(**defaults)  # type: ignore[arg-type]


def factory_crow_snapshot(
    *sessions: CrowSessionSummary,
    key: str = "k",
    as_of: datetime = FACTORY_DT,
) -> CrowSnapshot:
    """Build a CrowSnapshot from the given sessions."""
    return CrowSnapshot(sessions=sessions, as_of=as_of, invalidation_key=key)


def factory_crow_entry(**kwargs: Any) -> CrowEntry:
    """Build a CrowEntry; defaults model a healthy running crow."""
    defaults: dict[str, Any] = dict(
        agent_id="crow-t001",
        ticket_id="t001",
        ticket_title="Fix thing",
        harness="cursor",
        status="running",
        session="murder_demo_crow_t001",
        health=Health.GREEN,
    )
    defaults.update(kwargs)
    return CrowEntry(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Dispatch / schedule domain
# ---------------------------------------------------------------------------


def factory_ticket_summary(
    tid: str = "t001",
    status: Any = "planned",
    title: str = "Test ticket",
    harness: str | None = None,
    model: str | None = None,
) -> TicketSummary:
    """Build a TicketSummary."""
    return TicketSummary(
        id=tid, title=title, status=status, harness=harness, model=model
    )


def factory_dispatch_snapshot(
    tickets: tuple[TicketSummary, ...] = (),
    invalidation_key: str = "k1",
    as_of: datetime = FACTORY_DT,
) -> DispatchSnapshot:
    """Build a DispatchSnapshot."""
    return DispatchSnapshot(
        tickets=tickets, as_of=as_of, invalidation_key=invalidation_key
    )


def factory_schedule_ticket_row(
    tid: str = "t001",
    status: str = "planned",
    title: str = "Test ticket",
    last_update_at: datetime = FACTORY_DT,
) -> ScheduleTicketRow:
    """Build a ScheduleTicketRow."""
    return ScheduleTicketRow(
        id=tid,
        title=title,
        status=status,
        harness=None,
        model=None,
        last_update_at=last_update_at,
        last_update_label="auto",
        schedule_at=None,
        metadata_sync_state="synced",
        metadata_parse_error=None,
        metadata_conflict_reason=None,
        deps_ok=True,
    )


def factory_schedule_snapshot(
    active_tickets: tuple[ScheduleTicketRow, ...] = (),
    recent_done_tickets: tuple[ScheduleTicketRow, ...] = (),
    archived_tickets: tuple[ScheduleTicketRow, ...] = (),
    scheduler_mode: str = "manual",
    mode_rationale: str = "",
    usage_gauges: tuple[Any, ...] = (),
    invalidation_key: str = "k1",
    as_of: datetime = FACTORY_DT,
) -> ScheduleSnapshot:
    """Build a ScheduleSnapshot with empty optional buckets."""
    return ScheduleSnapshot(
        scheduler_mode=scheduler_mode,
        mode_rationale=mode_rationale,
        active_tickets=active_tickets,
        recent_done_tickets=recent_done_tickets,
        archived_tickets=archived_tickets,
        scheduler_decisions=(),
        usage_gauges=usage_gauges,
        calendar_harnesses=(),
        running_agents=(),
        scheduled_tickets=(),
        as_of=as_of,
        invalidation_key=invalidation_key,
    )


# ---------------------------------------------------------------------------
# Conversation domain
# ---------------------------------------------------------------------------


def factory_conversation_block(
    ordinal: int,
    kind: str = "user",
    payload: dict | None = None,
    block_id: int | None = None,
    conversation_id: str = "conv-1",
) -> ConversationBlockSummary:
    """Build a ConversationBlockSummary."""
    return ConversationBlockSummary(
        id=block_id,
        conversation_id=conversation_id,
        ordinal=ordinal,
        kind=kind,
        payload=payload or {"type": "user", "text": f"msg-{ordinal}"},
        sealed=True,
        service_received_at="2026-01-01T00:00:00",
    )


def factory_conversation_summary(
    conversation_id: str = "conv-1",
    agent_id: str = "agent-1",
    blocks: tuple[ConversationBlockSummary, ...] = (),
) -> ConversationSummary:
    """Build a ConversationSummary."""
    return ConversationSummary(
        conversation_id=conversation_id,
        agent_id=agent_id,
        harness="cc",
        model="sonnet",
        harness_session_id=None,
        live_state="awaiting_input",
        condensed=None,
        status="in_progress",
        blocks=blocks,
    )


def factory_conversations_snapshot(
    *summaries: ConversationSummary,
    key: str = "key",
    as_of: datetime = FACTORY_DT,
) -> ConversationsSnapshot:
    """Build a ConversationsSnapshot."""
    return ConversationsSnapshot(
        conversations=summaries, as_of=as_of, invalidation_key=key
    )


# ---------------------------------------------------------------------------
# Document domain (plans / notes / reports)
# ---------------------------------------------------------------------------


def factory_plan_summary(
    name: str = "plan-a",
    revision: int = 1,
    status: str = "active",
    sync: str = "clean",
) -> PlanSummary:
    """Build a PlanSummary."""
    return PlanSummary(
        name=name, status=status, revision_count=revision, sync_state=sync
    )


def factory_note_summary(
    name: str = "note-a",
    char_count: int = 100,
    updated: datetime = FACTORY_DT,
) -> NoteSummary:
    """Build a NoteSummary."""
    return NoteSummary(name=name, char_count=char_count, updated_at=updated)


def factory_report_summary(
    name: str = "report-a",
    char_count: int = 200,
    updated: datetime = FACTORY_DT,
) -> ReportSummary:
    """Build a ReportSummary."""
    return ReportSummary(name=name, char_count=char_count, updated_at=updated)


def factory_plans_snapshot(
    plans: tuple[PlanSummary, ...] = (),
    invalidation_key: str = "k1",
    as_of: datetime = FACTORY_DT,
) -> PlansSnapshot:
    """Build a PlansSnapshot."""
    return PlansSnapshot(
        plans=plans, invalidation_key=invalidation_key, as_of=as_of
    )


def factory_notes_snapshot(
    notes: tuple[NoteSummary, ...] = (),
    invalidation_key: str = "k1",
    as_of: datetime = FACTORY_DT,
) -> NotesSnapshot:
    """Build a NotesSnapshot."""
    return NotesSnapshot(
        notes=notes, invalidation_key=invalidation_key, as_of=as_of
    )


def factory_reports_snapshot(
    reports: tuple[ReportSummary, ...] = (),
    invalidation_key: str = "k1",
    as_of: datetime = FACTORY_DT,
) -> ReportsSnapshot:
    """Build a ReportsSnapshot."""
    return ReportsSnapshot(
        reports=reports, invalidation_key=invalidation_key, as_of=as_of
    )


# ---------------------------------------------------------------------------
# Escalation domain
# ---------------------------------------------------------------------------


def factory_escalation_row(
    escalation_id: int,
    *,
    ticket_id: str | None = "t-1",
    reason: str = "blocked",
    severity: int = 2,
    to_recipient: str = "user",
    ticket_status: str | None = None,
) -> EscalationSummary:
    """Build an EscalationSummary."""
    return EscalationSummary(
        id=escalation_id,
        ticket_id=ticket_id,
        severity=severity,
        reason=reason,
        to_recipient=to_recipient,
        body_path=None,
        ticket_status=ticket_status,
    )


def factory_escalations_snapshot(
    *active: EscalationSummary,
    history: tuple[EscalationSummary, ...] = (),
    key: str = "test",
    as_of: datetime = FACTORY_DT,
) -> EscalationsSnapshot:
    """Build an EscalationsSnapshot from active (and optional history) rows."""
    return EscalationsSnapshot(
        active=active,
        history=history,
        as_of=as_of,
        invalidation_key=key,
    )
