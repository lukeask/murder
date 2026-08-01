"""Persistence for the agents and agent_messages tables."""
# ruff: noqa: E501

from __future__ import annotations

from datetime import datetime
from typing import Any

from murder.roster.repository import RosterRepository
from murder.state.persistence.connection import RepoDb


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def upsert_agent(
    db: RepoDb,
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
    """Persist through the roster feature's atomic write boundary."""
    RosterRepository().sync_agent(
        db,
        agent_id=agent_id,
        role=role,
        ticket_id=ticket_id,
        session=session,
        harness=harness,
        model=model,
        status=status,
        start_commit=start_commit,
        worktree_path=worktree_path,
        pid=pid,
    )


# F11 H1 — heartbeat emit coalescing.
#
# A plain heartbeat only bumps ``last_heartbeat_at``. The roster projection
# derives the client-side
# "stuck" flag (Ink ``isStuck``: ``now - last_seen > STUCK_AFTER``, mirrored from
# Python ``read_model.STUCK_AFTER`` / ``crow_health.STUCK_AFTER = 60s``). The Ink
# roster is event-driven (re-pulled through ``projection.invalidate``. There is
# no client refetch timer), so we cannot drop the refresh input entirely
# or a healthy crow's ``last_seen`` would freeze and it would render as falsely
# "stuck" after 60s. But invalidating on every ~5s beat is the antipattern
# (a refetch storm Ink cannot use — it renders no sub-bucket heartbeat precision).
#
# Policy: invalidate only when ``floor(now / HEARTBEAT_EMIT_BUCKET_S)`` advances,
# i.e. at most once per bucket per agent. The bucket is half ``STUCK_AFTER`` so the
# client's worst-case ``last_seen`` staleness (bucket + bus latency) stays well under
# the 60s stuck threshold and a live crow never flips to false-stuck. Status changes
# go through the ``sync_agent`` choke point (which already appends roster input) and are
# unaffected by this gate.
HEARTBEAT_EMIT_BUCKET_S: float = 30.0


def heartbeat_agent(db: RepoDb, agent_id: str) -> None:
    RosterRepository().heartbeat_agent(db, agent_id=agent_id, invalidate=True)


def heartbeat_bucket(now_s: float, *, bucket_s: float = HEARTBEAT_EMIT_BUCKET_S) -> int:
    """The coalescing bucket index for a monotonic-clock reading ``now_s``.

    Pure integer arithmetic on an injected clock (no wall-clock, no sleep) so the
    emit-coalescing gate is fully deterministic under the test conftest's
    noop-``asyncio.sleep`` patch. The caller emits ``agent`` only when this index
    advances between heartbeats.
    """
    return int(now_s // max(1e-9, bucket_s))


def set_agent_status(db: RepoDb, agent_id: str, status: str) -> None:
    RosterRepository().set_agent_status(db, agent_id=agent_id, status=status)


def get_agent_status(db: RepoDb, agent_id: str) -> str | None:
    """Return the recorded status of an agent, or None if no row exists."""
    row = db.conn.execute(
        "SELECT status FROM agents WHERE repository_id = ? AND agent_id = ?",
        (db.repository_id, agent_id),
    ).fetchone()
    return str(row["status"]) if row is not None else None


def rename_agent(
    db: RepoDb,
    old_agent_id: str,
    new_agent_id: str,
    *,
    session: str | None = None,
) -> None:
    """Rekey an agent row and its stored transcript."""
    now = _now()
    old_row = db.conn.execute(
        "SELECT * FROM agents WHERE repository_id = ? AND agent_id = ?",
        (db.repository_id, old_agent_id),
    ).fetchone()
    new_row = db.conn.execute(
        "SELECT * FROM agents WHERE repository_id = ? AND agent_id = ?",
        (db.repository_id, new_agent_id),
    ).fetchone()
    if old_row is not None:
        if new_row is not None:
            db.conn.execute(
                "DELETE FROM agents WHERE repository_id = ? AND agent_id = ?",
                (db.repository_id, new_agent_id),
            )
        db.conn.execute(
            """
            UPDATE agents
               SET agent_id = ?, session = COALESCE(?, session), last_heartbeat_at = ?
             WHERE repository_id = ? AND agent_id = ?
            """,
            (new_agent_id, session, now, db.repository_id, old_agent_id),
        )
    elif new_row is not None and session is not None:
        db.conn.execute(
            "UPDATE agents SET session = ?, last_heartbeat_at = ? WHERE repository_id = ? AND agent_id = ?",
            (session, now, db.repository_id, new_agent_id),
        )
    db.conn.execute(
        "UPDATE agent_messages SET agent_id = ? WHERE repository_id = ? AND agent_id = ?",
        (new_agent_id, db.repository_id, old_agent_id),
    )


def get_active_agent_by_role(db: RepoDb, role: str) -> str | None:
    """Return the agent_id of a running/idle agent with the given role, or None."""
    row = db.conn.execute(
        "SELECT agent_id FROM agents WHERE repository_id = ? AND role = ? AND status IN ('running','idle') LIMIT 1",
        (db.repository_id, role),
    ).fetchone()
    return str(row["agent_id"]) if row else None


def get_agent_messages(db: RepoDb, agent_id: str) -> list[dict[str, Any]]:
    rows = db.conn.execute(
        "SELECT ordinal, role, body, captured_at FROM agent_messages "
        "WHERE repository_id = ? AND agent_id = ? ORDER BY ordinal",
        (db.repository_id, agent_id),
    ).fetchall()
    return [dict(r) for r in rows]


def replace_agent_messages(
    db: RepoDb,
    agent_id: str,
    turns: list[tuple[str, str]],
    *,
    captured_at: str | None = None,
) -> None:
    """Atomically replace the whole stored transcript for ``agent_id``."""
    ts = captured_at or _now()
    db.conn.execute(
        "DELETE FROM agent_messages WHERE repository_id = ? AND agent_id = ?",
        (db.repository_id, agent_id),
    )
    db.conn.executemany(
        "INSERT INTO agent_messages (repository_id, agent_id, ordinal, role, body, captured_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [(db.repository_id, agent_id, i, role, body, ts) for i, (role, body) in enumerate(turns)],
    )


def append_agent_message(
    db: RepoDb,
    agent_id: str,
    role: str,
    body: str,
    *,
    captured_at: str | None = None,
) -> None:
    """Append one message row without rewriting prior transcript history."""
    ts = captured_at or _now()
    row = db.conn.execute(
        "SELECT COALESCE(MAX(ordinal), -1) + 1 AS next_ordinal FROM agent_messages WHERE repository_id = ? AND agent_id = ?",
        (db.repository_id, agent_id),
    ).fetchone()
    ordinal = int(row["next_ordinal"]) if row is not None else 0
    db.conn.execute(
        "INSERT INTO agent_messages (repository_id, agent_id, ordinal, role, body, captured_at) VALUES (?, ?, ?, ?, ?, ?)",
        (db.repository_id, agent_id, ordinal, role, body, ts),
    )


def list_stale_done_crow_sessions(
    db: RepoDb,
    *,
    older_than_minutes: int = 10,
) -> list[dict[str, Any]]:
    """Return crow agents with a live session whose ticket reached a terminal state
    at least ``older_than_minutes`` ago.

    Returns list of dicts with keys: agent_id, session, ticket_id, worktree_path.
    """
    rows = db.conn.execute(
        """
        SELECT a.agent_id, a.session, a.ticket_id, a.worktree_path
          FROM agents a
          JOIN tickets t ON t.repository_id = a.repository_id AND a.ticket_id = t.id
         WHERE a.repository_id = ? AND a.role = 'crow'
           AND a.session IS NOT NULL
           AND t.status IN ('done', 'failed')
           AND datetime(t.updated_at) < datetime('now', ? || ' minutes')
        """,
        (db.repository_id, f"-{older_than_minutes}"),
    ).fetchall()
    return [dict(r) for r in rows]


def list_orphaned_planner_sessions(
    db: RepoDb,
    *,
    older_than_minutes: int = 30,
) -> list[dict[str, Any]]:
    """Return planner / planning_handler agents with a tmux session to reclaim.

    A row is orphaned when it has a non-NULL session AND either:
      (a) the agent's own status is terminal (dead/done/failed) — no time gate. Or
      (b) the owning plan (derived from the ``planner-<plan>`` /
          ``planning_handler-<plan>`` agent_id) is missing from the plans table,
          or has status 'superseded', AND that state has been stable for at least
          ``older_than_minutes``. The age clock uses the plan's ``updated_at`` when
          the plan row exists, else the agent row's ``started_at``.

    Live planners on draft/accepted plans are NEVER returned — they are
    plan-scoped and long-lived by design.

    Returns list of dicts with keys: agent_id, session, status.
    """
    candidates = db.conn.execute(
        """
        SELECT agent_id, session, status, role, started_at
          FROM agents
         WHERE repository_id = ? AND role IN ('planner', 'planning_handler')
           AND session IS NOT NULL
        """,
        (db.repository_id,),
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
                plan_name = agent_id[len(prefix) :]
                break
        if not plan_name:
            continue

        plan = db.conn.execute(
            "SELECT status, updated_at FROM plans WHERE repository_id = ? AND name = ?",
            (db.repository_id, plan_name),
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
            # No timestamp to gate on. Treat as old enough to reclaim.
            out.append({"agent_id": agent_id, "session": row["session"], "status": status})
            continue

        # datetime(?) normalizes our ISO-T timestamps ('2026-06-11T17:00:00')
        # to SQLite's space-separated form so the comparison is not defeated
        # by 'T' sorting above ' ' on same-day values.
        older = db.conn.execute(
            "SELECT datetime(?) < datetime('now', ? || ' minutes') AS is_old",
            (age_anchor, f"-{older_than_minutes}"),
        ).fetchone()
        if older is not None and older["is_old"]:
            out.append({"agent_id": agent_id, "session": row["session"], "status": status})

    return out


def clear_agent_session(db: RepoDb, agent_id: str) -> None:
    """NULL out the session column for an agent (used after killing its tmux session)."""
    db.conn.execute(
        "UPDATE agents SET session = NULL WHERE repository_id = ? AND agent_id = ?",
        (db.repository_id, agent_id),
    )
