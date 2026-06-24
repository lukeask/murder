"""Persistence helpers for harness usage sampling."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_usage_probe_session_id(conn: sqlite3.Connection, harness: str) -> str | None:
    row = conn.execute(
        "SELECT session_id FROM harness_usage_probe_sessions WHERE harness = ?",
        (harness,),
    ).fetchone()
    if row is None:
        return None
    session_id = row["session_id"]
    return str(session_id) if isinstance(session_id, str) and session_id else None


def set_usage_probe_session_id(
    conn: sqlite3.Connection,
    harness: str,
    session_id: str,
) -> None:
    session_id = session_id.strip()
    if not session_id:
        return
    conn.execute(
        """
        INSERT INTO harness_usage_probe_sessions (harness, session_id, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(harness) DO UPDATE SET
            session_id = excluded.session_id,
            updated_at = excluded.updated_at
        """,
        (harness, session_id, _now()),
    )


def clear_usage_probe_session_id(conn: sqlite3.Connection, harness: str) -> None:
    conn.execute(
        "DELETE FROM harness_usage_probe_sessions WHERE harness = ?",
        (harness,),
    )


__all__ = [
    "clear_usage_probe_session_id",
    "get_usage_probe_session_id",
    "set_usage_probe_session_id",
]
