"""Agent shutdown helpers extracted from ``Runtime.stop`` (W3 narrow)."""

from __future__ import annotations

import contextlib

from murder.agents.base import AgentStatus
from murder.service.agent_registry import AgentRegistry


async def shutdown_live_agents(
    registry: AgentRegistry,
    *,
    graceful: bool,
) -> None:
    """Stop all in-process agents; preserve tmux on graceful TUI quit."""
    terminal_statuses = {AgentStatus.DONE, AgentStatus.FAILED, AgentStatus.DEAD}
    for agent in registry.all_agents():
        with contextlib.suppress(Exception):
            await agent.stop(
                failed=agent.status not in terminal_statuses,
                kill_session=not graceful,
            )
    registry.clear()


__all__ = ["shutdown_live_agents"]
