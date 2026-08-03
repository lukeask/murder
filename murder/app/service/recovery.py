"""Startup reconciliation: DB agent rows vs live tmux sessions.

Called during Runtime.start() to detect and clean up zombie agents —
rows that claim to be running/idle but cannot be addressed after restart
(missing tmux pane, or a surviving pane with no in-process registry
registration — e.g. ticketless rogue crows after ``serviced`` restart).

This module is synchronous-DB-only. The caller fetches live sessions
and passes them in so the function is easily testable without tmux.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID, uuid5

from murder.runtime.sessions.contracts import (
    Correlation,
    PrincipalKind,
    PrincipalRef,
    SessionStatus,
)
from murder.runtime.sessions.persistence import SessionStore
from murder.state.persistence.agents import set_agent_status as _db_set_agent_status
from murder.state.persistence.connection import RepoDb
from murder.work.tickets import lifecycle
from murder.work.tickets.status import TicketStatus

LOGGER = logging.getLogger(__name__)
_RECOVERY_FACT_NAMESPACE = UUID("9b584cc6-113e-4cba-b845-e4df8498248d")


@dataclass
class ReconcileReport:
    agents_marked_dead: list[str] = field(default_factory=list)
    tickets_reset_to_failed: list[str] = field(default_factory=list)
    sessions_to_kill: list[str] = field(default_factory=list)
    harness_sessions_marked_lost: list[str] = field(default_factory=list)
    # in_progress tickets whose crow session is still alive after a restart:
    # (ticket_id, crow_session). The caller rehydrates an in-memory CrowAgent +
    # fresh handler so DONE is consumed and the ticket can finish.
    crows_to_reattach: list[tuple[str, str]] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(
            self.agents_marked_dead
            or self.tickets_reset_to_failed
            or self.sessions_to_kill
            or self.harness_sessions_marked_lost
            or self.crows_to_reattach
        )

    def summary(self) -> str:
        parts = []
        if self.agents_marked_dead:
            parts.append(f"marked dead: {', '.join(self.agents_marked_dead)}")
        if self.tickets_reset_to_failed:
            parts.append(f"tickets → failed: {', '.join(self.tickets_reset_to_failed)}")
        if self.sessions_to_kill:
            parts.append(f"sessions to kill: {', '.join(self.sessions_to_kill)}")
        if self.harness_sessions_marked_lost:
            parts.append("harness sessions lost: " + ", ".join(self.harness_sessions_marked_lost))
        if self.crows_to_reattach:
            parts.append(
                "crows to reattach: "
                + ", ".join(f"{tid}({session})" for tid, session in self.crows_to_reattach)
            )
        return "; ".join(parts) if parts else "nothing to reconcile"


# Statuses that we consider "should be live" — if the session is gone,
# the agent is a zombie and must be cleaned up.
_LIVE_STATUSES = frozenset({"running", "idle", "blocked", "escalating", "failed"})
_NON_RESUMABLE_ROLES = frozenset({"planner", "planning_handler"})


def reconcile_agents_vs_tmux(
    db: RepoDb,
    live_sessions: set[str],
) -> ReconcileReport:
    """Mark zombie agents dead. Recover tickets stuck in in_progress.

    Algorithm:
    1. For every non-terminal agent row that will not be rehydrated into the
       in-process registry, mark it ``dead``. Ticketed crows whose tmux pane
       survived are the only agents left alive for the reattach pass; orphan
       panes for rogues/collaborators/handlers are queued for kill so the
       roster never advertises a messageable agent that ``agent.message``
       cannot address.
    2. For every ticket in `in_progress` whose crow agent is now dead (or
       never registered), transition to `failed` so kickoff_ready can
       retry once the user (or recovery) reopens it.

    The `live_sessions` set should come from `tmux.list_sessions()` right
    before this call so the snapshot is fresh.
    """
    report = ReconcileReport()

    conn = db.conn
    rows = conn.execute(
        "SELECT agent_id, role, ticket_id, status, session FROM agents "
        "WHERE repository_id = ? "
        "AND status IN ('running','idle','blocked','escalating','failed')",
        (db.repository_id,),
    ).fetchall()

    for row in rows:
        session = row["session"]
        if row["role"] in _NON_RESUMABLE_ROLES:
            _db_set_agent_status(db, row["agent_id"], "dead")
            report.agents_marked_dead.append(row["agent_id"])
            if session:
                report.sessions_to_kill.append(session)
            continue
        # Only ticketed crows with a surviving pane are reattached into the
        # registry. Rogues (ticketless crows), collaborators, and handlers
        # keep a live tmux pane after serviced restart but have no runtime
        # handle — leave them in the roster as idle and agent.message fails
        # with "no agent named …". Reap them here.
        if (
            row["role"] == "crow"
            and row["ticket_id"]
            and session
            and session in live_sessions
        ):
            continue
        _db_set_agent_status(db, row["agent_id"], "dead")
        report.agents_marked_dead.append(row["agent_id"])
        if session and session in live_sessions:
            report.sessions_to_kill.append(session)

    # Treat panes we are about to kill as already gone so their harness
    # session rows flip to lost (and writers revoke) in this same pass.
    _reconcile_persisted_harness_sessions(
        db, live_sessions - set(report.sessions_to_kill), report
    )

    # Recover in_progress tickets whose crow agent is no longer live.
    in_progress = conn.execute(
        "SELECT id FROM tickets WHERE repository_id = ? AND status = 'in_progress'",
        (db.repository_id,),
    ).fetchall()
    for t_row in in_progress:
        tid = t_row["id"]
        crow_row = conn.execute(
            "SELECT status FROM agents WHERE agent_id = ? AND repository_id = ?",
            (f"crow-{tid}", db.repository_id),
        ).fetchone()
        crow_alive = (
            crow_row is not None
            and crow_row["status"] not in ("dead", "done", "failed")
            and (
                # The crow's session must also be live.
                conn.execute(
                    "SELECT session FROM agents WHERE agent_id = ? AND repository_id = ?",
                    (f"crow-{tid}", db.repository_id),
                ).fetchone()["session"]
                in live_sessions
            )
        )
        if not crow_alive:
            try:
                lifecycle.transition(db, tid, TicketStatus.FAILED)
                report.tickets_reset_to_failed.append(tid)
            except Exception:
                # Already in a terminal state or transition not allowed — skip.
                LOGGER.debug(
                    "zombie recovery: ticket %s transition to FAILED rejected"
                    " (likely already terminal)",
                    tid,
                    exc_info=True,
                )
            continue

        # Crow IS alive: rehydrate it on startup instead of leaving the ticket
        # stuck in in_progress with no handler to consume DONE.
        crow_session = conn.execute(
            "SELECT session FROM agents WHERE agent_id = ? AND repository_id = ?",
            (f"crow-{tid}", db.repository_id),
        ).fetchone()["session"]
        report.crows_to_reattach.append((tid, crow_session))

        # The old handler row (and its debug log-tail session) is stale. Mark it
        # dead so the fresh handler spawned during reattach re-registers cleanly.
        # The first loop may have already marked it dead if its tail session was
        # gone — make this pass idempotent.
        handler_row = conn.execute(
            "SELECT status, session FROM agents WHERE agent_id = ? AND repository_id = ?",
            (f"crow_handler-{tid}", db.repository_id),
        ).fetchone()
        if handler_row is not None and handler_row["status"] not in ("dead", "done", "failed"):
            _db_set_agent_status(db, f"crow_handler-{tid}", "dead")
            report.agents_marked_dead.append(f"crow_handler-{tid}")
            if handler_row["session"]:
                report.sessions_to_kill.append(handler_row["session"])

    return report


def _reconcile_persisted_harness_sessions(
    db: RepoDb,
    live_sessions: set[str],
    report: ReconcileReport,
) -> None:
    """Mark vanished tmux controller resources LOST and revoke their writers."""

    conn = db.conn
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'harness_sessions'"
    ).fetchone()
    if exists is None:
        return
    rows = conn.execute(
        """
        SELECT session_id, transport_ref, revision
        FROM harness_sessions
        WHERE repository_id = ?
          AND transport = 'tmux'
          AND status NOT IN ('stopped', 'failed', 'lost')
        """,
        (db.repository_id,),
    ).fetchall()
    now = datetime.now(timezone.utc)
    store = SessionStore(db)
    for row in rows:
        if str(row["transport_ref"]) in live_sessions:
            continue
        session_id = UUID(str(row["session_id"]))
        persisted = store.get_session(session_id)
        if persisted is None or persisted.revision != int(row["revision"]):
            continue
        lost = persisted.model_copy(
            update={
                "status": SessionStatus.LOST,
                "revision": persisted.revision + 1,
                "last_observed_at": now,
                "stopped_at": now,
            }
        )
        store.save_terminal_session(
            lost,
            expected_revision=persisted.revision,
            reason="tmux resource missing during startup reconciliation",
            actor=PrincipalRef(kind=PrincipalKind.SERVICE, id="startup-recovery"),
            correlation=Correlation(
                correlation_id=uuid5(
                    _RECOVERY_FACT_NAMESPACE,
                    f"{session_id}:{persisted.revision}:lost",
                )
            ),
        )
        report.harness_sessions_marked_lost.append(str(session_id))
