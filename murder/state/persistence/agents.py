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


# F11 H1 — heartbeat emit coalescing.
#
# A plain heartbeat only bumps ``last_heartbeat_at``; the ONLY thing the
# ``state.crow_snapshot`` consumer derives from that field is the client-side
# "stuck" flag (Ink ``isStuck``: ``now - last_seen > STUCK_AFTER``, mirrored from
# Python ``read_model.STUCK_AFTER`` / ``crow_health.STUCK_AFTER = 60s``). The Ink
# roster is event-driven (re-pulled ONLY on an ``agent``-entity ``state.snapshot``;
# there is no client refetch timer), so we cannot drop the heartbeat emit entirely
# or a healthy crow's ``last_seen`` would freeze and it would render as falsely
# "stuck" after 60s. But emitting ``agent`` on every ~5s beat is the antipattern
# (a refetch storm Ink can't use — it renders no sub-bucket heartbeat precision).
#
# Policy: emit ``agent`` only when ``floor(now / HEARTBEAT_EMIT_BUCKET_S)`` advances,
# i.e. at most once per bucket per agent. The bucket is half ``STUCK_AFTER`` so the
# client's worst-case ``last_seen`` staleness (bucket + bus latency) stays well under
# the 60s stuck threshold and a live crow never flips to false-stuck. Status changes
# go through the ``sync_agent`` choke point (which already emits ``agent``) and are
# unaffected by this gate.
HEARTBEAT_EMIT_BUCKET_S: float = 30.0


def heartbeat_agent(conn: sqlite3.Connection, agent_id: str) -> None:
    conn.execute(
        "UPDATE agents SET last_heartbeat_at = ? WHERE agent_id = ?",
        (_now(), agent_id),
    )


def heartbeat_bucket(now_s: float, *, bucket_s: float = HEARTBEAT_EMIT_BUCKET_S) -> int:
    """The coalescing bucket index for a monotonic-clock reading ``now_s``.

    Pure integer arithmetic on an injected clock (no wall-clock, no sleep) so the
    emit-coalescing gate is fully deterministic under the test conftest's
    noop-``asyncio.sleep`` patch. The caller emits ``agent`` only when this index
    advances between heartbeats.
    """
    return int(now_s // max(1e-9, bucket_s))


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


def append_agent_message(
    conn: sqlite3.Connection,
    agent_id: str,
    role: str,
    body: str,
    *,
    captured_at: str | None = None,
) -> None:
    """Append one message row without rewriting prior transcript history."""
    ts = captured_at or _now()
    row = conn.execute(
        "SELECT COALESCE(MAX(ordinal), -1) + 1 AS next_ordinal FROM agent_messages WHERE agent_id = ?",
        (agent_id,),
    ).fetchone()
    ordinal = int(row["next_ordinal"]) if row is not None else 0
    conn.execute(
        "INSERT INTO agent_messages (agent_id, ordinal, role, body, captured_at) VALUES (?, ?, ?, ?, ?)",
        (agent_id, ordinal, role, body, ts),
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


def list_orphaned_planner_sessions(
    conn: sqlite3.Connection,
    *,
    older_than_minutes: int = 30,
) -> list[dict[str, Any]]:
    """Return planner / planning_handler agents whose tmux session should be reclaimed.

    A row is orphaned when it has a non-NULL session AND either:
      (a) the agent's own status is terminal (dead/done/failed) — no time gate; or
      (b) the owning plan (derived from the ``planner-<plan>`` /
          ``planning_handler-<plan>`` agent_id) is missing from the plans table,
          or has status 'superseded', AND that state has been stable for at least
          ``older_than_minutes``. The age clock uses the plan's ``updated_at`` when
          the plan row exists, else the agent row's ``started_at``.

    Live planners on draft/accepted plans are NEVER returned — they are
    plan-scoped and long-lived by design.

    Returns list of dicts with keys: agent_id, session, status.
    """
    candidates = conn.execute(
        """
        SELECT agent_id, session, status, role, started_at
          FROM agents
         WHERE role IN ('planner', 'planning_handler')
           AND session IS NOT NULL
        """
    ).fetchall()

    terminal = {"dead", "done", "failed"}
    out: list[dict[str, Any]] = []
    for row in candidates:
        agent_id = row["agent_id"]
        status = row["status"]
        if status in terminal:
            out.append({"agent_id": agent_id, "session": row["session"], "status": status})
            continue

        # Derive plan name from the agent_id naming convention.
        plan_name: str | None = None
        for prefix in ("planner-", "planning_handler-"):
            if agent_id.startswith(prefix):
                plan_name = agent_id[len(prefix):]
                break
        if not plan_name:
            continue

        plan = conn.execute(
            "SELECT status, updated_at FROM plans WHERE name = ?",
            (plan_name,),
        ).fetchone()

        if plan is None:
            # Plan row missing — gate on the agent's own start time.
            age_anchor = row["started_at"]
        elif plan["status"] == "superseded":
            age_anchor = plan["updated_at"]
        else:
            # draft / accepted — live, never sweep.
            continue

        if age_anchor is None:
            # No timestamp to gate on; treat as old enough to reclaim.
            out.append({"agent_id": agent_id, "session": row["session"], "status": status})
            continue

        older = conn.execute(
            "SELECT ? < datetime('now', ? || ' minutes') AS is_old",
            (age_anchor, f"-{older_than_minutes}"),
        ).fetchone()
        if older is not None and older["is_old"]:
            out.append({"agent_id": agent_id, "session": row["session"], "status": status})

    return out


def clear_agent_session(conn: sqlite3.Connection, agent_id: str) -> None:
    """NULL out the session column for an agent (used after killing its tmux session)."""
    conn.execute("UPDATE agents SET session = NULL WHERE agent_id = ?", (agent_id,))
