"""Background worker that reclaims orphaned planner/planning_handler tmux sessions.

A planner (or its planning_handler) becomes orphaned when its agent row is
terminal, or when the plan it was scoped to is gone/superseded and has been so
for a while. Live planners on draft/accepted plans are NEVER swept — they are
plan-scoped and long-lived by design. See
``list_orphaned_planner_sessions`` for the exact predicate.
"""

from __future__ import annotations

import asyncio
import logging

from murder.runtime.workers.base import Worker, WorkerCtx, WorkerSpec

LOGGER = logging.getLogger(__name__)
ORPHAN_PLANNER_TTL_MINUTES = 30
SWEEP_INTERVAL_S = 60.0


class PlannerSessionSweeperWorker(Worker):
    def __init__(self, *, sweep_interval_s: float = SWEEP_INTERVAL_S) -> None:
        super().__init__(
            WorkerSpec(name="planner-session-sweeper", heartbeat_s=sweep_interval_s)
        )
        self._interval = sweep_interval_s

    async def run(self, ctx: WorkerCtx, stop_event: asyncio.Event) -> None:
        from murder.state.persistence.agents import (
            clear_agent_session,
            list_orphaned_planner_sessions,
            set_agent_status,
        )
        from murder.runtime.terminal import tmux

        terminal = {"dead", "done", "failed"}

        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self._interval)
                return  # stop requested
            except asyncio.TimeoutError:
                pass

            if ctx.db is None:
                continue

            rows = list_orphaned_planner_sessions(
                ctx.db, older_than_minutes=ORPHAN_PLANNER_TTL_MINUTES
            )
            for row in rows:
                agent_id = row["agent_id"]
                session = row["session"]
                status = row["status"]
                # Only clear the DB session row once the tmux session is actually
                # gone — otherwise a failing kill leaves a leaked session with no
                # DB row pointing at it, so it would never be swept again.
                try:
                    await tmux.kill_session(session)
                    LOGGER.info(
                        "swept orphaned planner session %s for agent %s", session, agent_id
                    )
                except Exception:
                    LOGGER.warning(
                        "failed to kill orphaned planner session %s for agent %s; "
                        "leaving DB row for retry",
                        session,
                        agent_id,
                        exc_info=True,
                    )
                    continue
                try:
                    clear_agent_session(ctx.db, agent_id)
                    if status not in terminal:
                        set_agent_status(ctx.db, agent_id, "dead")
                except Exception:
                    LOGGER.warning(
                        "failed to clear session row for agent %s", agent_id, exc_info=True
                    )
