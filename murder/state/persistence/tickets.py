"""Persistence for tickets, ticket_deps, ticket_skills, and checklist tables."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import TYPE_CHECKING

from murder.state.persistence.records import (
    ChecklistItemRecord,
    TicketRecord,
    ticket_record_from_row,
)

if TYPE_CHECKING:
    from murder.work.tickets.schema import Ticket


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def insert_ticket(conn: sqlite3.Connection, ticket: Ticket) -> None:
    """Insert ticket + its child rows in one transaction."""
    now = _now()
    conn.execute("BEGIN")
    try:
        conn.execute(
            """
            INSERT INTO tickets(id, title, wave, status, harness, model, attempts,
                                created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                now,
            ),
        )
        for dep in ticket.deps:
            conn.execute(
                "INSERT INTO ticket_deps(ticket_id, depends_on_id) VALUES (?, ?)",
                (ticket.id, dep),
            )
        for skill in ticket.skills:
            conn.execute(
                "INSERT INTO ticket_skills(ticket_id, skill) VALUES (?, ?)",
                (ticket.id, skill),
            )
        for item in ticket.checklist:
            conn.execute(
                "INSERT INTO checklist(ticket_id, ord, text, done) VALUES (?, ?, ?, ?)",
                (ticket.id, item.ord, item.text, 1 if item.done else 0),
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def apply_ticket_carve_payload(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    title: str,
    harness: str | None,
    model: str | None,
    deps: list[str],
    skills: list[str],
    checklist: list[str],
) -> None:
    """Replace deps, skills, checklist and update ticket title/harness/model.

    Caller must wrap in a transaction if combined with status changes.
    """
    conn.execute(
        """
        UPDATE tickets
           SET title = ?, harness = ?, model = ?, updated_at = ?
         WHERE id = ?
        """,
        (title, harness, model, _now(), ticket_id),
    )
    conn.execute("DELETE FROM ticket_deps WHERE ticket_id = ?", (ticket_id,))
    for dep in deps:
        conn.execute(
            "INSERT INTO ticket_deps(ticket_id, depends_on_id) VALUES (?, ?)",
            (ticket_id, dep),
        )
    conn.execute("DELETE FROM ticket_skills WHERE ticket_id = ?", (ticket_id,))
    for skill in skills:
        conn.execute(
            "INSERT INTO ticket_skills(ticket_id, skill) VALUES (?, ?)",
            (ticket_id, skill),
        )
    conn.execute("DELETE FROM checklist WHERE ticket_id = ?", (ticket_id,))
    for ord_, text in enumerate(checklist):
        conn.execute(
            "INSERT INTO checklist(ticket_id, ord, text, done) VALUES (?, ?, ?, 0)",
            (ticket_id, ord_, text),
        )


def get_ticket(conn: sqlite3.Connection, ticket_id: str) -> TicketRecord | None:
    """Return ticket + child rows as a typed record, or None."""
    row = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    if row is None:
        return None
    deps = [
        str(r["depends_on_id"])
        for r in conn.execute(
            "SELECT depends_on_id FROM ticket_deps WHERE ticket_id = ?", (ticket_id,)
        )
    ]
    skills = [
        str(r["skill"])
        for r in conn.execute("SELECT skill FROM ticket_skills WHERE ticket_id = ?", (ticket_id,))
    ]
    checklist = [
        ChecklistItemRecord(
            id=int(r["id"]),
            ord=int(r["ord"]),
            text=str(r["text"]),
            done=bool(r["done"]),
            done_at=r["done_at"],
        )
        for r in conn.execute(
            "SELECT id, ord, text, done, done_at FROM checklist WHERE ticket_id = ? ORDER BY ord",
            (ticket_id,),
        )
    ]
    return ticket_record_from_row(
        row,
        deps=deps,
        skills=skills,
        checklist=checklist,
    )


def list_tickets_by_status(conn: sqlite3.Connection, status: str) -> list[TicketRecord]:
    rows = conn.execute(
        "SELECT id FROM tickets WHERE status = ? ORDER BY wave, id", (status,)
    ).fetchall()
    out: list[TicketRecord] = []
    for r in rows:
        t = get_ticket(conn, str(r["id"]))
        if t is not None:
            out.append(t)
    return out


def list_tickets_in_wave(conn: sqlite3.Connection, wave: int) -> list[TicketRecord]:
    rows = conn.execute("SELECT id FROM tickets WHERE wave = ? ORDER BY id", (wave,)).fetchall()
    out: list[TicketRecord] = []
    for r in rows:
        t = get_ticket(conn, str(r["id"]))
        if t is not None:
            out.append(t)
    return out


def update_ticket_status(conn: sqlite3.Connection, ticket_id: str, new_status: str) -> None:
    conn.execute(
        "UPDATE tickets SET status = ?, updated_at = ? WHERE id = ?",
        (new_status, _now(), ticket_id),
    )


def get_ticket_status(conn: sqlite3.Connection, ticket_id: str) -> str | None:
    row = conn.execute("SELECT status FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    return row["status"] if row else None


def compute_ready(conn: sqlite3.Connection) -> list[str]:
    """Tickets whose deps are all ``done`` and that are currently ``ready``.

    A ticket with no deps qualifies trivially. Result is sorted by wave then id
    so kickoff order is stable.
    """
    rows = conn.execute(
        """
        SELECT t.id
          FROM tickets AS t
          WHERE t.status = 'ready'
            AND NOT EXISTS (
                SELECT 1 FROM ticket_deps AS d
                  JOIN tickets AS dep ON dep.id = d.depends_on_id
                 WHERE d.ticket_id = t.id
                   AND dep.status NOT IN ('done', 'archived')
            )
          ORDER BY t.wave, t.id
        """
    ).fetchall()
    return [r["id"] for r in rows]


def dependents_of(conn: sqlite3.Connection, ticket_id: str) -> list[str]:
    """Tickets that directly depend on ``ticket_id``."""
    rows = conn.execute(
        "SELECT ticket_id FROM ticket_deps WHERE depends_on_id = ?", (ticket_id,)
    ).fetchall()
    return [r["ticket_id"] for r in rows]


def set_checklist(conn: sqlite3.Connection, ticket_id: str, items: list[str]) -> None:
    """Replace a ticket's checklist. Used by Collaborator on carve."""
    conn.execute("BEGIN")
    try:
        conn.execute("DELETE FROM checklist WHERE ticket_id = ?", (ticket_id,))
        for ord_, text in enumerate(items):
            conn.execute(
                "INSERT INTO checklist(ticket_id, ord, text, done) VALUES (?, ?, ?, 0)",
                (ticket_id, ord_, text),
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def check_off_item(conn: sqlite3.Connection, ticket_id: str, item_text: str) -> bool:
    """Mark first matching unchecked item as done; return True iff matched."""
    row = conn.execute(
        """
        SELECT id FROM checklist
         WHERE ticket_id = ? AND done = 0 AND text = ?
         ORDER BY ord LIMIT 1
        """,
        (ticket_id, item_text),
    ).fetchone()
    if row is None:
        return False
    conn.execute(
        "UPDATE checklist SET done = 1, done_at = ? WHERE id = ?",
        (_now(), row["id"]),
    )
    return True


def all_checked(conn: sqlite3.Connection, ticket_id: str) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM checklist WHERE ticket_id = ? AND done = 0",
        (ticket_id,),
    ).fetchone()
    return int(row["n"]) == 0


def checklist_progress(conn: sqlite3.Connection, ticket_id: str) -> tuple[int, int]:
    row = conn.execute(
        """
        SELECT
            SUM(CASE WHEN done = 1 THEN 1 ELSE 0 END) AS done_n,
            COUNT(*) AS total
          FROM checklist WHERE ticket_id = ?
        """,
        (ticket_id,),
    ).fetchone()
    return int(row["done_n"] or 0), int(row["total"] or 0)
