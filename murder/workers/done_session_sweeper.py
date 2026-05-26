"""Background worker that kills stale crow tmux sessions after ticket completion."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from murder.workers.base import Worker, WorkerCtx, WorkerSpec

LOGGER = logging.getLogger(__name__)
DONE_SESSION_TTL_MINUTES = 10
SWEEP_INTERVAL_S = 60.0


class DoneSessionSweeperWorker(Worker):
    def __init__(self, *, sweep_interval_s: float = SWEEP_INTERVAL_S) -> None:
        super().__init__(WorkerSpec(name="done-session-sweeper", heartbeat_s=sweep_interval_s))
        self._interval = sweep_interval_s

    async def run(self, ctx: WorkerCtx, stop_event: asyncio.Event) -> None:
        from murder.persistence.agents import (
            clear_agent_session,
            list_stale_done_crow_sessions,
        )
        from murder.terminal import tmux

        while not stop_event.is_set():
            try:
                await asyncio.wait_for(
                    stop_event.wait(), timeout=self._interval
                )
                return  # stop requested
            except asyncio.TimeoutError:
                pass

            if ctx.db is None:
                continue

            rows = list_stale_done_crow_sessions(
                ctx.db, older_than_minutes=DONE_SESSION_TTL_MINUTES
            )
            for row in rows:
                agent_id = row["agent_id"]
                session = row["session"]
                with contextlib.suppress(Exception):
                    await tmux.kill_session(session)
                    LOGGER.info("swept stale crow session %s for agent %s", session, agent_id)
                with contextlib.suppress(Exception):
                    clear_agent_session(ctx.db, agent_id)
                    ctx.db.commit()
