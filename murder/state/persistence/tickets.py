"""Persistence for tickets, ticket_deps, and checklist tables."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import TYPE_CHECKING

from murder.state.persistence.records import (
    ChecklistItemRecord,
    TicketRecord,
    ticket_record_from_row,
)
from murder.work.workflows.service import notify_ticket_status

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
            INSERT INTO tickets(id, title, status, harness, model, parent_ticket_id,
                                attempts, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ticket.id,
                ticket.title,
                ticket.status.value,
                ticket.harness,
                ticket.model,
                ticket.parent_id,
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
    checklist: list[str],
) -> None:
    """Replace deps, checklist and update ticket title/harness/model.

    The delete+reinsert of deps and checklist is wrapped in a ``SAVEPOINT`` so the
    whole payload applies atomically (a mid-write FK violation or crash can't leave
    the ticket with its deps wiped and only some checklist rows back). SAVEPOINT
    nests safely whether or not the caller already has a transaction open (carve.py
    wraps this in ``BEGIN``; ticket_ops applies it on an autocommit connection).
    """
    conn.execute("SAVEPOINT carve_payload")
    try:
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
        conn.execute("DELETE FROM checklist WHERE ticket_id = ?", (ticket_id,))
        for ord_, text in enumerate(checklist):
            conn.execute(
                "INSERT INTO checklist(ticket_id, ord, text, done) VALUES (?, ?, ?, 0)",
                (ticket_id, ord_, text),
            )
        conn.execute("RELEASE carve_payload")
    except Exception:
        conn.execute("ROLLBACK TO carve_payload")
        conn.execute("RELEASE carve_payload")
        raise


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
        checklist=checklist,
    )


def list_tickets_by_status(conn: sqlite3.Connection, status: str) -> list[TicketRecord]:
    rows = conn.execute(
        "SELECT id FROM tickets WHERE status = ? ORDER BY id", (status,)
    ).fetchall()
    out: list[TicketRecord] = []
    for r in rows:
        t = get_ticket(conn, str(r["id"]))
        if t is not None:
            out.append(t)
    return out


def update_ticket_status(conn: sqlite3.Connection, ticket_id: str, new_status: str) -> None:
    owns_transaction = conn.isolation_level is None and not conn.in_transaction
    if owns_transaction:
        conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "UPDATE tickets SET status = ?, updated_at = ? WHERE id = ?",
            (new_status, _now(), ticket_id),
        )
        # Static ticket DAGs are a compatibility definition type, not the source
        # of workflow truth. Their terminal outcomes become addressed signals that
        # advance the authoritative persisted state machine in this same transaction.
        notify_ticket_status(conn, ticket_id=ticket_id, status=new_status)
    except BaseException:
        if owns_transaction:
            conn.rollback()
        raise
    else:
        if owns_transaction:
            conn.commit()


def get_ticket_status(conn: sqlite3.Connection, ticket_id: str) -> str | None:
    row = conn.execute("SELECT status FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    return row["status"] if row else None


def compute_ready(conn: sqlite3.Connection) -> list[str]:
    """Tickets currently ``ready`` whose every dep is ``done`` or ``archived``.

    A ticket with no deps qualifies trivially. Result is sorted by id so kickoff order is
    stable.
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
          ORDER BY t.id
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
    """Replace a ticket's checklist.

    Note: currently has no callers (carve applies its checklist through
    ``apply_ticket_carve_payload``); kept as a standalone checklist-replace helper.
    """
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
