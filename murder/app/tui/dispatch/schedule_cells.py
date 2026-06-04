"""Pure schedule-column projection from service snapshots (no SQL)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime, timedelta

from murder.app.service.client_api import ScheduleTicketRow, SchedulerDecisionSummary
from murder.work.tickets.status import TicketStatus


def _format_schedule_timestamp(value: str | None) -> str | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return str(value)
    return dt.strftime("%a %H:%M")


def _crow_dispatch_semantics(
    rows: Sequence[SchedulerDecisionSummary],
    ticket_id: str,
) -> str:
    if not rows:
        return "queued"
    if any(d.decision == 1 and d.kicked_ticket_id == ticket_id for d in rows):
        return "queued"
    if any(d.decision == 0 and d.rationale.startswith("Holding:") for d in rows):
        return "waiting"
    return "queued"


def _crow_rows_for_harness(
    harness: str | None,
    all_rows: Sequence[SchedulerDecisionSummary],
    by_harness: dict[str, list[SchedulerDecisionSummary]],
) -> list[SchedulerDecisionSummary]:
    if harness is None:
        return list(all_rows)
    return list(by_harness.get(harness, ()))


def dispatch_schedule_cell(
    *,
    scheduler_mode: str,
    row: ScheduleTicketRow,
    decisions: Sequence[SchedulerDecisionSummary],
) -> str:
    st = row.status
    if st == TicketStatus.DONE.value:
        return ""
    if st == TicketStatus.IN_PROGRESS.value:
        return "now"
    ts = _format_schedule_timestamp(row.schedule_at)
    if ts is not None:
        return ts
    if st not in {TicketStatus.PLANNED.value, TicketStatus.READY.value, TicketStatus.DRAFT.value}:
        return ""
    if scheduler_mode == "manual":
        return "unscheduled"
    if scheduler_mode == "autorun_ready":
        return "queued"
    by_harness: dict[str, list[SchedulerDecisionSummary]] = defaultdict(list)
    for d in decisions:
        by_harness[d.harness].append(d)
    crow = _crow_rows_for_harness(row.harness, decisions, by_harness)
    return _crow_dispatch_semantics(crow, row.id)


def display_status_for(row: ScheduleTicketRow) -> str:
    sync_state = row.metadata_sync_state or "synced"
    return row.status if sync_state == "synced" else f"{row.status}!"


def last_update_cell(row: ScheduleTicketRow, as_of: datetime) -> str:
    stamp = (
        row.last_update_at.strftime("%H:%M")
        if as_of - row.last_update_at < timedelta(hours=24)
        else row.last_update_at.strftime("%Y-%m-%d")
    )
    return f"{stamp} {row.last_update_label}"


def deps_cell_for(row: ScheduleTicketRow) -> str:
    if row.status in {"planned", "ready"}:
        return "ok" if row.deps_ok else "wait"
    return "—"
