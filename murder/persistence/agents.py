"""Persistence for the agents, agent_messages, and sentinel_state tables."""

from __future__ import annotations

import json
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
    status: str,
    start_commit: str | None = None,
    pid: int | None = None,
) -> None:
    """Insert or update an agent row."""
    now = _now()
    existing = conn.execute("SELECT 1 FROM agents WHERE agent_id = ?", (agent_id,)).fetchone()
    if existing is None:
        conn.execute(
            """
            INSERT INTO agents
                (agent_id, role, ticket_id, session, status, start_commit,
                 started_at, last_heartbeat_at, pid)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (agent_id, role, ticket_id, session, status, start_commit, now, now, pid),
        )
    else:
        conn.execute(
            """
            UPDATE agents
               SET role = ?, ticket_id = ?, session = ?, status = ?,
                   start_commit = COALESCE(?, start_commit),
                   last_heartbeat_at = ?,
                   pid = COALESCE(?, pid)
             WHERE agent_id = ?
            """,
            (role, ticket_id, session, status, start_commit, now, pid, agent_id),
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


def upsert_sentinel_state(
    conn: sqlite3.Connection,
    *,
    key: str,
    run_id: str,
    state: dict[str, Any],
) -> None:
    conn.execute(
        """
        INSERT INTO sentinel_state(key, run_id, updated_at, state_json)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET
            run_id = excluded.run_id,
            updated_at = excluded.updated_at,
            state_json = excluded.state_json
        """,
        (key, run_id, _now(), json.dumps(state, default=str)),
    )


def get_sentinel_state(conn: sqlite3.Connection, key: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT state_json FROM sentinel_state WHERE key = ?",
        (key,),
    ).fetchone()
    if row is None:
        return None
    raw = row["state_json"]
    if not raw:
        return {}
    loaded = json.loads(raw)
    return loaded if isinstance(loaded, dict) else {}
