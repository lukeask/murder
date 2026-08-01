"""Subticket (parent->child) plumbing: column + migration round-trip, read-side
TicketSummary.parent, and the schedule wire DTO carrying parent/schedule_at/unmet-deps.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from murder.app.protocol.read_models import ScheduleTicketRow, TicketSummary
from murder.app.service.schedule_snapshot import build_schedule_snapshot
from murder.state.persistence import tickets as ticket_db
from murder.state.persistence.connection import Connection
from murder.state.persistence.migrations import _migrate_ticket_parent
from murder.work.tickets.schema import Ticket
from murder.work.tickets.status import TicketStatus
from tests.support.database import open_test_repo_db

NOW = datetime(2026, 1, 1, 12, 0, 0)


def _column_names(conn: Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def test_fresh_schema_has_parent_ticket_id_column(tmp_path: Path) -> None:
    db = open_test_repo_db(tmp_path / "murder.db")
    assert "parent_ticket_id" in _column_names(db.conn, "tickets")


def test_migration_adds_parent_ticket_id_to_existing_db(tmp_path: Path) -> None:
    # This is deliberately raw legacy setup: the migration must accept an
    # already-existing tickets table that predates parent_ticket_id.
    db = open_test_repo_db(tmp_path / "murder.db", initialize=False)
    conn = db.conn
    conn.execute(
        "CREATE TABLE tickets (id TEXT PRIMARY KEY, title TEXT NOT NULL, status TEXT NOT NULL)"
    )
    assert "parent_ticket_id" not in _column_names(conn, "tickets")

    _migrate_ticket_parent(conn)
    assert "parent_ticket_id" in _column_names(conn, "tickets")


def test_insert_ticket_round_trips_parent_id(tmp_path: Path) -> None:
    conn = open_test_repo_db(tmp_path / "murder.db")
    ticket_db.insert_ticket(
        conn,
        Ticket(id="t001", title="Parent", status=TicketStatus.DONE, created_at=NOW, updated_at=NOW),
    )
    ticket_db.insert_ticket(
        conn,
        Ticket(
            id="t002",
            title="Child",
            status=TicketStatus.READY,
            parent_id="t001",
            created_at=NOW,
            updated_at=NOW,
        ),
    )
    child = ticket_db.get_ticket(conn, "t002")
    parent = ticket_db.get_ticket(conn, "t001")
    assert child is not None and child.parent_id == "t001"
    assert parent is not None and parent.parent_id is None


def test_ticket_summary_carries_parent_field() -> None:
    summary = TicketSummary(
        id="t002", title="Child", status=TicketStatus.READY, harness=None, model=None, parent="t001"
    )
    assert summary.parent == "t001"
    # Default is None (existing callers unaffected).
    top = TicketSummary(id="t001", title="P", status=TicketStatus.DONE, harness=None, model=None)
    assert top.parent is None


def test_schedule_dto_carries_parent_schedule_at_and_unmet_deps(tmp_path: Path) -> None:
    conn = open_test_repo_db(tmp_path / "murder.db")
    # Dep that is NOT satisfied (ready) -> should be reported pending.
    ticket_db.insert_ticket(
        conn,
        Ticket(
            id="t001",
            title="Dep open",
            status=TicketStatus.READY,
            created_at=NOW,
            updated_at=NOW,
        ),
    )
    # Dep that IS satisfied (archived counts as satisfied, mirroring compute_ready).
    ticket_db.insert_ticket(
        conn,
        Ticket(
            id="t003",
            title="Dep arch",
            status=TicketStatus.ARCHIVED,
            created_at=NOW,
            updated_at=NOW,
        ),
    )
    ticket_db.insert_ticket(
        conn,
        Ticket(
            id="t002",
            title="Child",
            status=TicketStatus.PLANNED,
            deps=["t001", "t003"],
            parent_id="t001",
            created_at=NOW,
            updated_at=NOW,
        ),
    )
    conn.conn.execute(
        "UPDATE tickets SET schedule_at = '2026-02-01T10:00:00' "
        "WHERE repository_id = ? AND id = 't002'",
        (conn.repository_id,),
    )

    snap = build_schedule_snapshot(conn, as_of=NOW, invalidation_key="k")
    rows = {r.id: r for r in snap.active_tickets}
    child = rows["t002"]
    assert isinstance(child, ScheduleTicketRow)
    assert child.parent == "t001"
    assert child.schedule_at == "2026-02-01T10:00:00"
    # Only the unsatisfied dep (t001) is pending; the archived dep (t003) is satisfied.
    assert child.pending_dep_ids == ("t001",)
