"""Startup reconciliation: DB agent rows vs live tmux sessions.

Called during Runtime.start() to detect and clean up zombie agents —
rows that claim to be running/idle but whose tmux sessions are gone
(TUI crash, kill -9, system reboot, etc.).

This module is synchronous-DB-only; the caller fetches live sessions
and passes them in so the function is easily testable without tmux.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from murder import db as dbmod
from murder.bus import TicketStatus
from murder.tickets import lifecycle


@dataclass
class ReconcileReport:
    agents_marked_dead: list[str] = field(default_factory=list)
    tickets_reset_to_failed: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.agents_marked_dead or self.tickets_reset_to_failed)

    def summary(self) -> str:
        parts = []
        if self.agents_marked_dead:
            parts.append(f"marked dead: {', '.join(self.agents_marked_dead)}")
        if self.tickets_reset_to_failed:
            parts.append(f"tickets → failed: {', '.join(self.tickets_reset_to_failed)}")
        return "; ".join(parts) if parts else "nothing to reconcile"


# Statuses that we consider "should be live" — if the session is gone,
# the agent is a zombie and must be cleaned up.
_LIVE_STATUSES = frozenset({"running", "idle", "blocked", "escalating", "failed"})


def reconcile_agents_vs_tmux(
    conn: sqlite3.Connection,
    live_sessions: set[str],
) -> ReconcileReport:
    """Mark zombie agents dead; recover tickets stuck in in_progress.

    Algorithm:
    1. For every non-terminal agent row, if its session name is not in
       `live_sessions`, mark it `dead`.
    2. For every ticket in `in_progress` whose crow agent is now dead (or
       never registered), transition to `failed` so kickoff_ready can
       retry once the user (or recovery) reopens it.

    The `live_sessions` set should come from `tmux.list_sessions()` right
    before this call so the snapshot is fresh.
    """
    report = ReconcileReport()

    rows = conn.execute(
        "SELECT agent_id, role, ticket_id, status, session FROM agents "
        "WHERE status IN ('running','idle','blocked','escalating','failed')"
    ).fetchall()

    for row in rows:
        session = row["session"]
        # Agents with no session (pure-coroutine roles) are managed entirely
        # by the runtime; skip them — they'll be re-registered on startup.
        if not session:
            continue
        if session not in live_sessions:
            dbmod.set_agent_status(conn, row["agent_id"], "dead")
            report.agents_marked_dead.append(row["agent_id"])

    # Recover in_progress tickets whose crow agent is no longer live.
    in_progress = conn.execute(
        "SELECT id FROM tickets WHERE status = 'in_progress'"
    ).fetchall()
    for t_row in in_progress:
        tid = t_row["id"]
        crow_row = conn.execute(
            "SELECT status FROM agents WHERE agent_id = ?",
            (f"crow-{tid}",),
        ).fetchone()
        crow_alive = (
            crow_row is not None
            and crow_row["status"] not in ("dead", "done", "failed")
            and (
                # The crow's session must also be live.
                conn.execute(
                    "SELECT session FROM agents WHERE agent_id = ?",
                    (f"crow-{tid}",),
                ).fetchone()["session"]
                in live_sessions
            )
        )
        if not crow_alive:
            try:
                lifecycle.transition(conn, tid, TicketStatus.FAILED)
                report.tickets_reset_to_failed.append(tid)
            except Exception:
                # Already in a terminal state or transition not allowed — skip.
                pass

    return report
