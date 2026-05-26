"""Agent ABC + lifecycle.

All roles implement this interface. `CrowAgent` and
`CollaboratorAgent` own real tmux sessions (interactive harness).
`PlanningAgent` also owns a real tmux session, cwd=.murder/.
`CrowHandler` and `PlanningHandler` are coroutines (D1) — their `session`
attribute names a logfile-tail tmux session for debug, not a real
interactive one.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

# Re-export from bus to keep StrEnum definitions in one place.
from murder.bus import AgentStatus
from murder.bus import Role as AgentRole

__all__ = ["Agent", "AgentRole", "AgentStatus"]


class Agent(ABC):
    id: str
    role: AgentRole
    session: str  # tmux session name (interactive) or virtual session (logfile-tail)
    status: AgentStatus
    ticket_id: str | None  # None for Collaborator, PlanningAgent, PlanningHandler

    @abstractmethod
    async def start(self, brief: str, ctx: dict[str, Any]) -> None:
        """Bring the agent online. For Crows: spawn tmux + harness;
        send the system prompt. For native daemons: kick off the loop."""

    @abstractmethod
    async def stop(self, *, failed: bool = False, kill_session: bool = True) -> None:
        """Shut down. Idempotent.

        kill_session=False leaves the tmux session alive so a subsequent
        Runtime.start() can detect and reattach (graceful TUI quit path).
        """

    @abstractmethod
    async def send(self, msg: str) -> None:
        """Deliver a message to the agent. For Crows: send-keys via harness.
        For PlanningAgent: send-keys via harness. For handlers: ignore by default."""

    async def is_live(self) -> bool:
        """Return True if the agent session is currently running.

        Tmux-based agents override this to check session existence; coroutine
        agents use status. Default is True so non-overriding agents are
        considered live unless explicitly stopped.
        """
        return True

    async def tick(self) -> None:
        """Optional cadence hook. CrowHandler uses this for its poll loop;
        others noop."""
        return None

    def attach_hint(self) -> str:
        from murder.terminal.tmux import attach_command

        return attach_command(self.session)
