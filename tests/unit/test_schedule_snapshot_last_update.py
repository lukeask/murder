from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from textual.app import App, ComposeResult

from murder.state.persistence.schema import get_db, init_db
from murder.app.service.client_api import ScheduleSnapshot, ScheduleTicketRow
from murder.app.service.schedule_snapshot import build_schedule_snapshot
from murder.state.storage.paths import db_path
from murder.work.tickets.schema import Ticket
from murder.work.tickets.status import TicketStatus
from murder.app.tui.dispatch.roster import ScheduleTicketsTable
from murder.app.tui.dispatch.schedule_cells import last_update_cell


def _row(
    ticket_id: str,
    *,
    updated_at: datetime,
    status: str = "planned",
    label: str = "content",
) -> ScheduleTicketRow:
    return ScheduleTicketRow(
        id=ticket_id,
        title=f"Ticket {ticket_id}",
        wave=1,
        status=status,
        last_update_at=updated_at,
        last_update_label=label,
        schedule_at=None,
        harness=None,
        model=None,
        metadata_sync_state="synced",
        metadata_parse_error=None,
        metadata_conflict_reason=None,
        deps_ok=True,
    )


def test_schedule_snapshot_exposes_last_update_fields_and_active_order(repo_root) -> None:
    conn = get_db(db_path(repo_root))
    init_db(conn)
    now = datetime(2026, 6, 2, 12, 0, 0)
    tickets = (
        Ticket(
            id="t100",
            title="failed",
            wave=1,
            status=TicketStatus.FAILED,
            created_at=now,
            updated_at=now - timedelta(minutes=5),
        ),
        Ticket(
            id="t101",
            title="conflict",
            wave=1,
            status=TicketStatus.PLANNED,
            created_at=now,
            updated_at=now - timedelta(minutes=15),
        ),
        Ticket(
            id="t102",
            title="parse",
            wave=1,
            status=TicketStatus.READY,
            created_at=now,
            updated_at=now - timedelta(minutes=30),
        ),
        Ticket(
            id="t103",
            title="failed only",
            wave=1,
            status=TicketStatus.FAILED,
            created_at=now,
            updated_at=now - timedelta(minutes=45),
        ),
    )
    for ticket in tickets:
        conn.execute(
            """
            INSERT INTO tickets(
                id, title, wave, status, harness, model, attempts, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ticket.id,
                ticket.title,
                ticket.wave,
                ticket.status.value,
                ticket.harness,
                ticket.model,
                ticket.attempts,
                ticket.created_at.isoformat(timespec="seconds"),
                ticket.updated_at.isoformat(timespec="seconds"),
            ),
        )
    conn.execute(
        """
        UPDATE tickets
           SET last_error = ?, metadata_sync_state = ?, metadata_conflict_reason = ?
         WHERE id = ?
        """,
        ("boom", "conflict", "yaml drift", "t100"),
    )
    conn.execute(
        """
        UPDATE tickets
           SET metadata_sync_state = ?, metadata_conflict_reason = ?
         WHERE id = ?
        """,
        ("conflict", "file mismatch", "t101"),
    )
    conn.execute(
        """
        UPDATE tickets
           SET metadata_sync_state = ?, metadata_parse_error = ?
         WHERE id = ?
        """,
        ("parse_error", "bad yaml", "t102"),
    )
    conn.execute("UPDATE tickets SET last_error = ? WHERE id = ?", ("boom", "t103"))
    conn.commit()

    snapshot = build_schedule_snapshot(conn, as_of=now, invalidation_key="k")

    assert [row.id for row in snapshot.active_tickets] == ["t100", "t101", "t102", "t103"]
    assert snapshot.active_tickets[0].last_update_at == now - timedelta(minutes=5)
    assert snapshot.active_tickets[0].last_update_label == "metadata conflict"
    assert snapshot.active_tickets[1].last_update_label == "metadata conflict"
    assert snapshot.active_tickets[2].last_update_label == "metadata parse error"
    assert snapshot.active_tickets[3].last_update_label == "status failed"


def test_last_update_cell_formats_recent_and_older_dates() -> None:
    as_of = datetime(2026, 6, 2, 12, 0, 0)

    assert (
        last_update_cell(
            _row(
                "t100",
                updated_at=datetime(2026, 6, 2, 3, 45, 0),
                label="status failed",
            ),
            as_of,
        )
        == "03:45 status failed"
    )
    assert (
        last_update_cell(
            _row(
                "t101",
                updated_at=datetime(2026, 5, 30, 23, 59, 0),
                label="content",
            ),
            as_of,
        )
        == "2026-05-30 content"
    )


def test_schedule_tickets_table_sorts_by_last_update_desc() -> None:
    now = datetime(2026, 6, 2, 12, 0, 0)
    snapshot = ScheduleSnapshot(
        scheduler_mode="manual",
        mode_rationale="",
        active_tickets=(
            _row("t200", updated_at=now - timedelta(hours=3)),
            _row("t201", updated_at=now - timedelta(minutes=10)),
        ),
        recent_done_tickets=(
            _row("t199", updated_at=now - timedelta(minutes=20), status="done"),
        ),
        archived_tickets=(
            _row("t198", updated_at=now - timedelta(days=2), status="archived"),
        ),
        scheduler_decisions=(),
        usage_gauges=(),
        calendar_harnesses=(),
        running_agents=(),
        scheduled_tickets=(),
        as_of=now,
        invalidation_key="k",
    )

    class _TableApp(App[None]):
        def compose(self) -> ComposeResult:
            yield ScheduleTicketsTable()

    async def _run() -> None:
        app = _TableApp()
        async with app.run_test() as pilot:
            table = app.query_one(ScheduleTicketsTable)
            table.refresh_from_snapshot(snapshot)
            await pilot.pause()
            assert table._ids == ["t201", "t199", "t200", "t198"]
            assert table.get_row_at(0)[4] == "11:50 content"

    asyncio.run(_run())
