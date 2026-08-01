"""Persistence helpers for the history_status overlay table.

The durable history *spine* is ``conversation_blocks`` rows with ``kind='user'``
(written at the send boundary). This module owns the thin overlay that records
an explicit terminal status per item, keyed by ``"<conversation_id>:<ordinal>"``.
v0 only ever writes ``'dismissed'``; the later LLM resolver writes richer
statuses into the same table without a schema change.
"""

from __future__ import annotations

from datetime import datetime

from murder.state.persistence.connection import RepoDb


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def set_history_status(
    db: RepoDb,
    item_id: str,
    status: str,
    status_note: str | None = None,
) -> None:
    """Upsert the overlay status for one history item.

    ``item_id`` is ``"<conversation_id>:<ordinal>"``. Idempotent: re-setting an
    item replaces its status/note and bumps ``updated_at``.
    """
    db.conn.execute(
        """
        INSERT INTO history_status (repository_id, item_id, status, status_note, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(repository_id, item_id) DO UPDATE SET
            status = excluded.status,
            status_note = excluded.status_note,
            updated_at = excluded.updated_at
        """,
        (db.repository_id, item_id, status, status_note, _now()),
    )


def get_status_map(db: RepoDb) -> dict[str, tuple[str, str | None]]:
    """Return ``{item_id: (status, status_note)}`` for every overlay row.

    The read model joins this against the user-block spine so status derivation
    stays a single pass (no per-row query).
    """
    rows = db.conn.execute(
        "SELECT item_id, status, status_note FROM history_status WHERE repository_id = ?",
        (db.repository_id,),
    ).fetchall()
    return {str(r["item_id"]): (str(r["status"]), r["status_note"]) for r in rows}


__all__ = ["set_history_status", "get_status_map"]
