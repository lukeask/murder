"""Persistence for the agents and agent_messages tables."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def upsert_agent(
    conn: sqlite3.Connection,
    *,
    agent_id: str,
    role: str,
    ticket_id: str | None,
    session: str | None,
    harness: str | None = None,
    model: str | None = None,
    status: str,
    start_commit: str | None = None,
    worktree_path: str | None = None,
    pid: int | None = None,
) -> None:
    """Insert or update an agent row."""
    now = _now()
    existing = conn.execute("SELECT 1 FROM agents WHERE agent_id = ?", (agent_id,)).fetchone()
    if existing is None:
        conn.execute(
            """
            INSERT INTO agents
                (agent_id, role, ticket_id, session, harness, model, worktree_path, status,
                 start_commit, started_at, last_heartbeat_at, pid)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                agent_id,
                role,
                ticket_id,
                session,
                harness,
                model,
                worktree_path,
                status,
                start_commit,
                now,
                now,
                pid,
            ),
        )
    else:
        conn.execute(
            """
            UPDATE agents
               SET role = ?, ticket_id = ?, session = ?, harness = COALESCE(?, harness),
                   model = COALESCE(?, model),
                   worktree_path = COALESCE(?, worktree_path),
                   status = ?,
                   start_commit = COALESCE(?, start_commit),
                   last_heartbeat_at = ?,
                   pid = COALESCE(?, pid)
             WHERE agent_id = ?
            """,
            (
                role,
                ticket_id,
                session,
                harness,
                model,
                worktree_path,
                status,
                start_commit,
                now,
                pid,
                agent_id,
            ),
        )


def heartbeat_agent(conn: sqlite3.Connection, agent_id: str) -> None:
    conn.execute(
        "UPDATE agents SET last_heartbeat_at = ? WHERE agent_id = ?",
        (_now(), agent_id),
    )


def set_agent_status(conn: sqlite3.Connection, agent_id: str, status: str) -> None:
    conn.execute(
        "UPDATE agents SET status = ?, last_heartbeat_at = ? WHERE agent_id = ?",
        (status, _now(), agent_id),
    )


def rename_agent(
    conn: sqlite3.Connection,
    old_agent_id: str,
    new_agent_id: str,
    *,
    session: str | None = None,
) -> None:
    """Rekey an agent row and its stored transcript."""
    now = _now()
    old_row = conn.execute(
        "SELECT * FROM agents WHERE agent_id = ?", (old_agent_id,)
    ).fetchone()
    new_row = conn.execute(
        "SELECT * FROM agents WHERE agent_id = ?", (new_agent_id,)
    ).fetchone()
    if old_row is not None:
        if new_row is not None:
            conn.execute("DELETE FROM agents WHERE agent_id = ?", (new_agent_id,))
        conn.execute(
            """
            UPDATE agents
               SET agent_id = ?, session = COALESCE(?, session), last_heartbeat_at = ?
             WHERE agent_id = ?
            """,
            (new_agent_id, session, now, old_agent_id),
        )
    elif new_row is not None and session is not None:
        conn.execute(
            "UPDATE agents SET session = ?, last_heartbeat_at = ? WHERE agent_id = ?",
            (session, now, new_agent_id),
        )
    conn.execute(
        "UPDATE agent_messages SET agent_id = ? WHERE agent_id = ?",
        (new_agent_id, old_agent_id),
    )


def get_active_agent_by_role(conn: sqlite3.Connection, role: str) -> str | None:
    """Return the agent_id of a running/idle agent with the given role, or None."""
    row = conn.execute(
        "SELECT agent_id FROM agents WHERE role = ? AND status IN ('running','idle') LIMIT 1",
        (role,),
    ).fetchone()
    return str(row["agent_id"]) if row else None


def get_agent_messages(conn: sqlite3.Connection, agent_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT ordinal, role, body, captured_at FROM agent_messages "
        "WHERE agent_id = ? ORDER BY ordinal",
        (agent_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def replace_agent_messages(
    conn: sqlite3.Connection,
    agent_id: str,
    turns: list[tuple[str, str]],
    *,
    captured_at: str | None = None,
) -> None:
    """Atomically replace the whole stored transcript for ``agent_id``."""
    ts = captured_at or _now()
    conn.execute("DELETE FROM agent_messages WHERE agent_id = ?", (agent_id,))
    conn.executemany(
        "INSERT INTO agent_messages (agent_id, ordinal, role, body, captured_at) "
        "VALUES (?, ?, ?, ?, ?)",
        [(agent_id, i, role, body, ts) for i, (role, body) in enumerate(turns)],
    )


def list_stale_done_crow_sessions(
    conn: sqlite3.Connection,
    *,
    older_than_minutes: int = 10,
) -> list[dict[str, Any]]:
    """Return crow agents with a live session whose ticket reached a terminal state
    at least ``older_than_minutes`` ago.

    Returns list of dicts with keys: agent_id, session, ticket_id, worktree_path.
    """
    rows = conn.execute(
        """
        SELECT a.agent_id, a.session, a.ticket_id, a.worktree_path
          FROM agents a
          JOIN tickets t ON a.ticket_id = t.id
         WHERE a.role = 'crow'
           AND a.session IS NOT NULL
           AND t.status IN ('done', 'failed')
           AND t.updated_at < datetime('now', ? || ' minutes')
        """,
        (f"-{older_than_minutes}",),
    ).fetchall()
    return [dict(r) for r in rows]


def clear_agent_session(conn: sqlite3.Connection, agent_id: str) -> None:
    """NULL out the session column for an agent (used after killing its tmux session)."""
    conn.execute("UPDATE agents SET session = NULL WHERE agent_id = ?", (agent_id,))
