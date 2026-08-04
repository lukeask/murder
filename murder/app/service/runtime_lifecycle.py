"""Project-wide tmux sweep retained outside AgentRuntime (W3 / Phase 2)."""

from __future__ import annotations

import asyncio

from murder.runtime.terminal import tmux
from murder.runtime.terminal.session_names import SessionNamePolicy


async def kill_project_tmux_sessions(session_names: SessionNamePolicy) -> list[str]:
    """Kill every tmux session owned by this murder project.

    This is the authoritative service-stop sweep. Registered agents are stopped
    first via ``AgentRuntime.close()``. This catches any project-scoped sessions
    that are no longer in the in-memory registry.
    """
    prefix = session_names.project_prefix()
    sessions = await tmux.list_sessions(prefix=prefix)
    await asyncio.gather(
        *(tmux.kill_session(session) for session in sessions),
        return_exceptions=True,
    )
    return sessions


__all__ = ["kill_project_tmux_sessions"]
