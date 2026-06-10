"""Agent shutdown helpers extracted from ``Runtime.stop`` (W3 narrow)."""

from __future__ import annotations

import asyncio
import contextlib

from murder.runtime.agents.base import AgentStatus, LifecycleParticipant
from murder.app.service.agent_registry import AgentRegistry
from murder.runtime.terminal import tmux
from murder.runtime.terminal.session_names import project_session_prefix


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


async def kill_project_tmux_sessions(scope: object) -> list[str]:
    """Kill every tmux session owned by this murder project.

    This is the authoritative service-stop sweep. Registered agents are stopped
    first; this catches any project-scoped sessions that are no longer in the
    in-memory registry.
    """
    prefix = project_session_prefix(scope)  # type: ignore[arg-type]
    sessions = await tmux.list_sessions(prefix=prefix)
    await asyncio.gather(
        *(tmux.kill_session(session) for session in sessions),
        return_exceptions=True,
    )
    return sessions


__all__ = ["kill_project_tmux_sessions", "shutdown_live_agents"]
