"""Status transition rules (D7)."""

from __future__ import annotations

import sqlite3

import pytest

from murder import db as dbmod
from murder.bus import TicketStatus
from murder.tickets.lifecycle import (
    VALID_TRANSITIONS,
    InvalidTransition,
    clear_last_error,
    set_last_error,
    transition,
    reopen,
)


def _seed_ticket(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    status: str = "planned",
    wave: int = 1,
) -> None:
    conn.execute(
        "INSERT INTO tickets(id, title, wave, status, attempts, created_at, updated_at) "
        "VALUES (?, 'test', ?, ?, 0, '2024-01-01', '2024-01-01')",
        (ticket_id, wave, status),
    )


def test_done_can_reopen_to_planned() -> None:
    """D7: 'we were wrong' path."""
    assert TicketStatus.PLANNED in VALID_TRANSITIONS[TicketStatus.DONE]


def test_failed_to_planned_only() -> None:
    assert VALID_TRANSITIONS[TicketStatus.FAILED] == {TicketStatus.PLANNED}


def test_planned_to_in_progress_is_blocked() -> None:
    """planned must pass through ready first."""
    assert TicketStatus.IN_PROGRESS not in VALID_TRANSITIONS[TicketStatus.PLANNED]


def test_invalid_transition_raises(memdb: sqlite3.Connection) -> None:
    _seed_ticket(memdb, "t001", status="planned")
    with pytest.raises(InvalidTransition):
        transition(memdb, "t001", TicketStatus.DONE)


def test_reopen_cascades_dependents(memdb: sqlite3.Connection) -> None:
    _seed_ticket(memdb, "t001", status="done")
    _seed_ticket(memdb, "t002", status="ready")
    memdb.execute(
        "INSERT INTO ticket_deps(ticket_id, depends_on_id) VALUES ('t002', 't001')"
    )
    cascaded = reopen(memdb, "t001")
    assert cascaded == ["t002"]
    assert dbmod.get_ticket_status(memdb, "t001") == "planned"
    assert dbmod.get_ticket_status(memdb, "t002") == "planned"


def test_failed_to_planned_transition(memdb: sqlite3.Connection) -> None:
    _seed_ticket(memdb, "t003", status="failed")
    prev = transition(memdb, "t003", TicketStatus.PLANNED, reason="retry")
    assert prev == TicketStatus.FAILED
    assert dbmod.get_ticket_status(memdb, "t003") == "planned"


def test_clear_last_error(memdb: sqlite3.Connection) -> None:
    _seed_ticket(memdb, "t004", status="failed")
    set_last_error(memdb, "t004", "something broke")
    row = memdb.execute("SELECT last_error FROM tickets WHERE id = 't004'").fetchone()
    assert row["last_error"] == "something broke"

    clear_last_error(memdb, "t004")
    row = memdb.execute("SELECT last_error FROM tickets WHERE id = 't004'").fetchone()
    assert row["last_error"] is None


def test_retry_failed_clears_error(memdb: sqlite3.Connection) -> None:
    _seed_ticket(memdb, "t005", status="failed")
    set_last_error(memdb, "t005", "crow timed out")
    transition(memdb, "t005", TicketStatus.PLANNED, reason="retry")
    clear_last_error(memdb, "t005")
    assert dbmod.get_ticket_status(memdb, "t005") == "planned"
    row = memdb.execute("SELECT last_error FROM tickets WHERE id = 't005'").fetchone()
    assert row["last_error"] is None
