"""One-shot boot reconciliation workflow.

Coordinates workflow-signal recovery, agent/session/ticket reconcile, stale
tmux kills, conversation marking, and claim/reservation reaping. Returns a
typed result; Crow reattachment stays a post-socket background task.
"""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass
from uuid import UUID

from murder.app.service.recovery import ReconcileReport, reconcile_agents_vs_tmux
from murder.runtime.terminal import tmux
from murder.state.persistence.activities import (
    reap_expired_claims,
    reap_expired_reservations,
)
from murder.state.persistence.connection import RepoDb
from murder.state.persistence.conversation import mark_stale_conversations
from murder.work.workflows.service import WorkflowRuntime

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class CrowReattachment:
    ticket_id: str
    crow_session: str

    def __iter__(self):
        yield self.ticket_id
        yield self.crow_session


@dataclass(frozen=True)
class StartupRecoveryResult:
    agents_marked_dead: tuple[str, ...] = ()
    tickets_reset_to_failed: tuple[str, ...] = ()
    sessions_killed: tuple[str, ...] = ()
    harness_sessions_marked_lost: tuple[UUID, ...] = ()
    crows_to_reattach: tuple[CrowReattachment, ...] = ()
    stale_conversations_marked: int = 0

    def __bool__(self) -> bool:
        return bool(
            self.agents_marked_dead
            or self.tickets_reset_to_failed
            or self.sessions_killed
            or self.harness_sessions_marked_lost
            or self.crows_to_reattach
            or self.stale_conversations_marked
        )

    def summary(self) -> str:
        parts: list[str] = []
        if self.agents_marked_dead:
            parts.append(f"marked dead: {', '.join(self.agents_marked_dead)}")
        if self.tickets_reset_to_failed:
            parts.append(f"tickets → failed: {', '.join(self.tickets_reset_to_failed)}")
        if self.sessions_killed:
            parts.append(f"sessions killed: {', '.join(self.sessions_killed)}")
        if self.harness_sessions_marked_lost:
            parts.append(
                "harness sessions lost: "
                + ", ".join(str(s) for s in self.harness_sessions_marked_lost)
            )
        if self.crows_to_reattach:
            parts.append(
                "crows to reattach: "
                + ", ".join(
                    f"{c.ticket_id}({c.crow_session})" for c in self.crows_to_reattach
                )
            )
        if self.stale_conversations_marked:
            parts.append(f"stale conversations: {self.stale_conversations_marked}")
        return "; ".join(parts) if parts else "nothing to reconcile"


def _from_reconcile_report(
    report: ReconcileReport,
    *,
    sessions_killed: tuple[str, ...],
    stale_conversations_marked: int,
) -> StartupRecoveryResult:
    return StartupRecoveryResult(
        agents_marked_dead=tuple(report.agents_marked_dead),
        tickets_reset_to_failed=tuple(report.tickets_reset_to_failed),
        sessions_killed=sessions_killed,
        harness_sessions_marked_lost=tuple(
            UUID(s) for s in report.harness_sessions_marked_lost
        ),
        crows_to_reattach=tuple(
            CrowReattachment(ticket_id=tid, crow_session=session)
            for tid, session in report.crows_to_reattach
        ),
        stale_conversations_marked=stale_conversations_marked,
    )


async def run_startup_recovery(*, db: RepoDb) -> StartupRecoveryResult:
    """Run the synchronous boot reconciliation path before socket bind.

    Current reconciliation is DB + tmux only. Surviving Crows are returned for
    post-socket reattachment — they are not reattached here.
    """
    WorkflowRuntime(db).recover_pending_signals()
    live_sessions = set(await tmux.list_sessions())
    report = reconcile_agents_vs_tmux(db, live_sessions)
    killed: list[str] = []
    for session in report.sessions_to_kill:
        with contextlib.suppress(Exception):
            await tmux.kill_session(session)
            killed.append(session)
    stale_count = mark_stale_conversations(db)
    if report or stale_count:
        LOGGER.info(
            "startup reconcile: %s%s",
            report.summary() if report else "nothing to reconcile",
            f"; stale conversations: {stale_count}" if stale_count else "",
        )
    reap_expired_claims(db)
    reap_expired_reservations(db)
    return _from_reconcile_report(
        report,
        sessions_killed=tuple(killed),
        stale_conversations_marked=stale_count,
    )


__all__ = [
    "CrowReattachment",
    "StartupRecoveryResult",
    "run_startup_recovery",
]
