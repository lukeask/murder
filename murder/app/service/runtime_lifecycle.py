"""Agent shutdown helpers extracted from ``Runtime.stop`` (W3 narrow)."""

from __future__ import annotations

import asyncio
import contextlib

from murder.runtime.agents.base import AgentStatus, LifecycleParticipant
from murder.app.service.agent_registry import AgentRegistry


async def shutdown_live_agents(
    registry: AgentRegistry,
    *,
    graceful: bool,
) -> None:
    """Stop all in-process agents; preserve tmux on graceful TUI quit.

    Each ``agent.stop`` makes several serial ``tmux`` round-trips, so stopping
    agents one at a time made shutdown latency scale with the number of crows
    (and held the repo flock the whole time, racing the next ``murder``). Stop
    them concurrently instead; per-agent failures are swallowed individually.
    """
    terminal_statuses = {AgentStatus.DONE, AgentStatus.FAILED, AgentStatus.DEAD}

    async def _stop_one(agent: LifecycleParticipant) -> None:
        with contextlib.suppress(Exception):
            await agent.stop(
                failed=agent.status not in terminal_statuses,
                kill_session=not graceful,
            )

    await asyncio.gather(*(_stop_one(agent) for agent in registry.all_agents()))
    registry.clear()


__all__ = ["shutdown_live_agents"]
