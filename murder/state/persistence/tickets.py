"""Persistence for tickets, ticket_deps, and checklist tables."""
# ruff: noqa: E501

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID, uuid5

from murder.facts.contracts import ProjectionInputDraft
from murder.facts.log import append_projection_input
from murder.roster.repository import RosterRepository
from murder.state.persistence.connection import RepoDb
from murder.state.persistence.records import (
    ChecklistItemRecord,
    TicketRecord,
    ticket_record_from_row,
)
from murder.work.workflows.service import notify_ticket_status

if TYPE_CHECKING:
    from murder.work.tickets.schema import Ticket

# Stable namespace for schedule projection input ids (ticket status invalidations).
_SCHEDULE_PROJECTION_NAMESPACE = UUID("a8c3e1f0-5b2d-4e9a-9c1f-7d6e4b3a2f10")


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def invalidate_ticket_schedule(
    db: RepoDb,
    *,
    ticket_id: str,
    operation: str,
) -> None:
    """Append a key-only schedule invalidation without inventing a retained fact."""

    row = db.conn.execute(
        """
        SELECT COALESCE(MAX(generation), -1) + 1 AS next_gen
          FROM projection_inputs
         WHERE repository_id = ? AND projection = 'schedule' AND subject_key = ?
        """,
        (db.repository_id, ticket_id),
    ).fetchone()
    generation = int(row["next_gen"])
    timestamp = datetime.now(timezone.utc)
    append_projection_input(
        db,
        ProjectionInputDraft(
            input_id=uuid5(
                _SCHEDULE_PROJECTION_NAMESPACE,
                f"{ticket_id}:{generation}:{operation}",
            ),
            projection="schedule",
            subject_key=ticket_id,
            generation=generation,
        ),
        created_at=timestamp,
    )


def insert_ticket(db: RepoDb, ticket: Ticket) -> None:
    """Insert ticket + its child rows in one transaction."""
    now = _now()
    db.conn.execute("BEGIN IMMEDIATE")
    try:
        db.conn.execute(
            """
            INSERT INTO tickets(repository_id, id, title, status, harness, model, parent_ticket_id,
                                attempts, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                db.repository_id,
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
            db.conn.execute(
                "INSERT INTO ticket_deps(repository_id, ticket_id, depends_on_id) VALUES (?, ?, ?)",
                (db.repository_id, ticket.id, dep),
            )
        for item in ticket.checklist:
            db.conn.execute(
                "INSERT INTO checklist(repository_id, ticket_id, ord, text, done) VALUES (?, ?, ?, ?, ?)",
                (db.repository_id, ticket.id, item.ord, item.text, 1 if item.done else 0),
            )
        invalidate_ticket_schedule(db, ticket_id=ticket.id, operation="insert")
        RosterRepository().invalidate(db, subject_key=f"ticket:{ticket.id}")
        db.conn.execute("COMMIT")
    except Exception:
        db.conn.execute("ROLLBACK")
        raise


def apply_ticket_carve_payload(
    db: RepoDb,
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
    whole payload applies atomically (a mid-write FK violation or crash cannot leave
    the ticket with its deps wiped and only some checklist rows back). SAVEPOINT
    nests safely whether or not the caller already has a transaction open (carve.py
    wraps this in ``BEGIN``. ticket_ops applies it on an autocommit connection).
    """
    db.conn.execute("SAVEPOINT carve_payload")
    try:
        db.conn.execute(
            """
            UPDATE tickets
               SET title = ?, harness = ?, model = ?, updated_at = ?
             WHERE repository_id = ? AND id = ?
            """,
            (title, harness, model, _now(), db.repository_id, ticket_id),
        )
        db.conn.execute(
            "DELETE FROM ticket_deps WHERE repository_id = ? AND ticket_id = ?",
            (db.repository_id, ticket_id),
        )
        for dep in deps:
            db.conn.execute(
                "INSERT INTO ticket_deps(repository_id, ticket_id, depends_on_id) VALUES (?, ?, ?)",
                (db.repository_id, ticket_id, dep),
            )
        db.conn.execute(
            "DELETE FROM checklist WHERE repository_id = ? AND ticket_id = ?",
            (db.repository_id, ticket_id),
        )
        for ord_, text in enumerate(checklist):
            db.conn.execute(
                "INSERT INTO checklist(repository_id, ticket_id, ord, text, done) VALUES (?, ?, ?, ?, 0)",
                (db.repository_id, ticket_id, ord_, text),
            )
        invalidate_ticket_schedule(db, ticket_id=ticket_id, operation="carve")
        RosterRepository().invalidate(db, subject_key=f"ticket:{ticket_id}")
        db.conn.execute("RELEASE carve_payload")
    except Exception:
        db.conn.execute("ROLLBACK TO carve_payload")
        db.conn.execute("RELEASE carve_payload")
        raise


def get_ticket(db: RepoDb, ticket_id: str) -> TicketRecord | None:
    """Return ticket + child rows as a typed record, or None."""
    row = db.conn.execute(
        "SELECT * FROM tickets WHERE repository_id = ? AND id = ?", (db.repository_id, ticket_id)
    ).fetchone()
    if row is None:
        return None
    deps = [
        str(r["depends_on_id"])
        for r in db.conn.execute(
            "SELECT depends_on_id FROM ticket_deps WHERE repository_id = ? AND ticket_id = ?",
            (db.repository_id, ticket_id),
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
        for r in db.conn.execute(
            "SELECT id, ord, text, done, done_at FROM checklist WHERE repository_id = ? AND ticket_id = ? ORDER BY ord",
            (db.repository_id, ticket_id),
        )
    ]
    return ticket_record_from_row(
        row,
        deps=deps,
        checklist=checklist,
    )


def list_tickets_by_status(db: RepoDb, status: str) -> list[TicketRecord]:
    rows = db.conn.execute(
        "SELECT id FROM tickets WHERE repository_id = ? AND status = ? ORDER BY id",
        (db.repository_id, status),
    ).fetchall()
    out: list[TicketRecord] = []
    for r in rows:
        t = get_ticket(db, str(r["id"]))
        if t is not None:
            out.append(t)
    return out


def update_ticket_status(db: RepoDb, ticket_id: str, new_status: str) -> None:
    conn = db.conn
    owns_transaction = conn.isolation_level is None and not conn.in_transaction
    if owns_transaction:
        conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "UPDATE tickets SET status = ?, updated_at = ? WHERE repository_id = ? AND id = ?",
            (new_status, _now(), db.repository_id, ticket_id),
        )
        # Static ticket DAGs are a compatibility definition type, not the source
        # of workflow truth. Their terminal outcomes become addressed signals that
        # advance the authoritative persisted state machine in this same transaction.
        notify_ticket_status(db, ticket_id=ticket_id, status=new_status)
        # Feature-owned schedule projection invalidation.
        invalidate_ticket_schedule(db, ticket_id=ticket_id, operation=f"status:{new_status}")
        RosterRepository().invalidate(db, subject_key=f"ticket:{ticket_id}")
    except BaseException:
        if owns_transaction:
            conn.rollback()
        raise
    else:
        if owns_transaction:
            conn.commit()


def update_ticket_schedule_at(db: RepoDb, ticket_id: str, schedule_at: str | None) -> None:
    """Update scheduling metadata and its schedule projection atomically."""
    conn = db.conn
    owns_transaction = conn.isolation_level is None and not conn.in_transaction
    if owns_transaction:
        conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "UPDATE tickets SET schedule_at = ?, updated_at = ? WHERE repository_id = ? AND id = ?",
            (schedule_at, _now(), db.repository_id, ticket_id),
        )
        invalidate_ticket_schedule(db, ticket_id=ticket_id, operation="schedule_at")
    except BaseException:
        if owns_transaction:
            conn.rollback()
        raise
    else:
        if owns_transaction:
            conn.commit()


def get_ticket_status(db: RepoDb, ticket_id: str) -> str | None:
    row = db.conn.execute(
        "SELECT status FROM tickets WHERE repository_id = ? AND id = ?",
        (db.repository_id, ticket_id),
    ).fetchone()
    return row["status"] if row else None


def compute_ready(db: RepoDb) -> list[str]:
    """Tickets currently ``ready`` whose every dep is ``done`` or ``archived``.

    A ticket with no deps qualifies trivially. Result is sorted by id so kickoff order is
    stable.
    """
    rows = db.conn.execute(
        """
        SELECT t.id
          FROM tickets AS t
          WHERE t.repository_id = ? AND t.status = 'ready'
            AND NOT EXISTS (
                SELECT 1 FROM ticket_deps AS d
                  JOIN tickets AS dep ON dep.repository_id = d.repository_id AND dep.id = d.depends_on_id
                 WHERE d.repository_id = t.repository_id AND d.ticket_id = t.id
                   AND dep.status NOT IN ('done', 'archived')
            )
          ORDER BY t.id
        """,
        (db.repository_id,),
    ).fetchall()
    return [r["id"] for r in rows]


def dependents_of(db: RepoDb, ticket_id: str) -> list[str]:
    """Tickets that directly depend on ``ticket_id``."""
    rows = db.conn.execute(
        "SELECT ticket_id FROM ticket_deps WHERE repository_id = ? AND depends_on_id = ?",
        (db.repository_id, ticket_id),
    ).fetchall()
    return [r["ticket_id"] for r in rows]


def set_checklist(db: RepoDb, ticket_id: str, items: list[str]) -> None:
    """Replace a ticket's checklist.

    Note: currently has no callers (carve applies its checklist through
    ``apply_ticket_carve_payload``). Kept as a standalone checklist-replace helper.
    """
    db.conn.execute("BEGIN IMMEDIATE")
    try:
        db.conn.execute(
            "DELETE FROM checklist WHERE repository_id = ? AND ticket_id = ?",
            (db.repository_id, ticket_id),
        )
        for ord_, text in enumerate(items):
            db.conn.execute(
                "INSERT INTO checklist(repository_id, ticket_id, ord, text, done) VALUES (?, ?, ?, ?, 0)",
                (db.repository_id, ticket_id, ord_, text),
            )
        db.conn.execute("COMMIT")
    except Exception:
        db.conn.execute("ROLLBACK")
        raise


def check_off_item(db: RepoDb, ticket_id: str, item_text: str) -> bool:
    """Mark the first matching unchecked item as done. Return True if matched."""
    row = db.conn.execute(
        """
        SELECT id FROM checklist
         WHERE repository_id = ? AND ticket_id = ? AND done = 0 AND text = ?
         ORDER BY ord LIMIT 1
        """,
        (db.repository_id, ticket_id, item_text),
    ).fetchone()
    if row is None:
        return False
    db.conn.execute(
        "UPDATE checklist SET done = 1, done_at = ? WHERE repository_id = ? AND id = ?",
        (_now(), db.repository_id, row["id"]),
    )
    return True


def all_checked(db: RepoDb, ticket_id: str) -> bool:
    row = db.conn.execute(
        "SELECT COUNT(*) AS n FROM checklist WHERE repository_id = ? AND ticket_id = ? AND done = 0",
        (db.repository_id, ticket_id),
    ).fetchone()
    return int(row["n"]) == 0


def checklist_progress(db: RepoDb, ticket_id: str) -> tuple[int, int]:
    row = db.conn.execute(
        """
        SELECT
            SUM(CASE WHEN done = 1 THEN 1 ELSE 0 END) AS done_n,
            COUNT(*) AS total
          FROM checklist WHERE repository_id = ? AND ticket_id = ?
        """,
        (db.repository_id, ticket_id),
    ).fetchone()
    return int(row["done_n"] or 0), int(row["total"] or 0)
