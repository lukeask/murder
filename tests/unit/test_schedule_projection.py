"""Feature-owned schedule projection: write-path invalidation + broker snapshot."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from murder.facts.log import replay_projection_inputs
from murder.state.persistence import tickets as ticket_db
from murder.state.persistence.schema import get_db, init_db
from murder.work.tickets.schema import Ticket
from murder.work.tickets.status import TicketStatus

NOW = datetime(2026, 1, 1, 12, 0, 0)


class _NoopBus:
    async def publish(self, event: Any) -> None:
        pass


def _conn(tmp_path: Path) -> sqlite3.Connection:
    conn = get_db(tmp_path / "murder.db")
    init_db(conn)
    return conn


def test_update_ticket_status_appends_schedule_projection_input(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    ticket_db.insert_ticket(
        conn,
        Ticket(
            id="t001",
            title="Schedule me",
            status=TicketStatus.READY,
            created_at=NOW,
            updated_at=NOW,
        ),
    )

    ticket_db.update_ticket_status(conn, "t001", TicketStatus.IN_PROGRESS.value)
    ticket_db.update_ticket_status(conn, "t001", TicketStatus.DONE.value)

    inputs = replay_projection_inputs(conn, projection="schedule")
    assert len(inputs) == 3
    assert all(item.subject_key == "t001" for item in inputs)
    assert [item.generation for item in inputs] == [0, 1, 2]
    assert inputs[0].source_fact_id is None
    assert inputs[1].source_fact_id is None


def test_projection_snapshot_schedule_returns_expected_shape(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    ticket_db.insert_ticket(
        conn,
        Ticket(
            id="t001",
            title="Active ticket",
            status=TicketStatus.READY,
            harness="codex",
            model="gpt-5",
            created_at=NOW,
            updated_at=NOW,
        ),
    )
    ticket_db.update_ticket_status(conn, "t001", TicketStatus.IN_PROGRESS.value)

    from murder.app.service.schedule_snapshot import build_schedule_snapshot
    from murder.app.protocol.read_models import dto_to_wire

    snap = dto_to_wire(
        build_schedule_snapshot(conn, as_of=NOW, invalidation_key="schedule-0")
    )

    assert isinstance(snap, dict)
    assert snap["scheduler_mode"] == "manual"
    assert snap["invalidation_key"] == "schedule-0"
    assert "active_tickets" in snap
    assert "recent_done_tickets" in snap
    assert "archived_tickets" in snap
    assert "scheduler_decisions" in snap
    assert "usage_gauges" in snap
    assert "calendar_harnesses" in snap
    assert "running_agents" in snap
    assert "scheduled_tickets" in snap
    assert "as_of" in snap
    assert "mode_rationale" in snap

    active_ids = {row["id"] for row in snap["active_tickets"]}
    assert "t001" in active_ids
    active = next(row for row in snap["active_tickets"] if row["id"] == "t001")
    assert active["status"] == "in_progress"
    assert active["title"] == "Active ticket"
