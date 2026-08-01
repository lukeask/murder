"""Persistence helpers for harness usage sampling."""

# ruff: noqa: E501

from __future__ import annotations

from datetime import datetime, timezone

from murder.state.persistence.connection import RepoDb


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_usage_probe_session_id(db: RepoDb, harness: str) -> str | None:
    row = db.conn.execute(
        "SELECT session_id FROM harness_usage_probe_sessions WHERE repository_id = ? AND harness = ?",
        (db.repository_id, harness),
    ).fetchone()
    if row is None:
        return None
    session_id = row["session_id"]
    return str(session_id) if isinstance(session_id, str) and session_id else None


def set_usage_probe_session_id(
    db: RepoDb,
    harness: str,
    session_id: str,
) -> None:
    session_id = session_id.strip()
    if not session_id:
        return
    db.conn.execute(
        """
        INSERT INTO harness_usage_probe_sessions (repository_id, harness, session_id, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(repository_id, harness) DO UPDATE SET
            session_id = excluded.session_id,
            updated_at = excluded.updated_at
        """,
        (db.repository_id, harness, session_id, _now()),
    )


def clear_usage_probe_session_id(db: RepoDb, harness: str) -> None:
    db.conn.execute(
        "DELETE FROM harness_usage_probe_sessions WHERE repository_id = ? AND harness = ?",
        (db.repository_id, harness),
    )


__all__ = [
    "clear_usage_probe_session_id",
    "get_usage_probe_session_id",
    "set_usage_probe_session_id",
]
