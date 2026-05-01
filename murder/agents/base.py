"""Agent ABC + lifecycle.

All four roles implement this interface. `MonkeyAgent` and
`CollaboratorAgent` own real tmux sessions (interactive harness).
`AugurAgent` and `SentinelAgent` are coroutines (D1) — their `session`
attribute names a logfile-tail tmux session for debug, not a real
interactive one.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

# Re-export from bus to keep StrEnum definitions in one place.
from murder.bus import AgentStatus, Role as AgentRole

__all__ = ["Agent", "AgentRole", "AgentStatus"]


class Agent(ABC):
    id: str
    role: AgentRole
    session: str  # tmux session name (interactive) or virtual session (logfile-tail)
    status: AgentStatus
    ticket_id: str | None  # None for Collaborator, Sentinel

    @abstractmethod
    async def start(self, brief: str, ctx: dict[str, Any]) -> None:
        """Bring the agent online. For Monkeys: spawn tmux + harness;
        send the system prompt. For native daemons: kick off the loop."""

    @abstractmethod
    async def stop(self) -> None:
        """Shut down. Idempotent."""

    @abstractmethod
    async def send(self, msg: str) -> None:
        """Deliver a message to the agent. For Monkeys: send-keys via harness.
        For Sentinel: queue an event for its handler. For Augur: ignore by default."""

    async def tick(self) -> None:
        """Optional cadence hook. Augur uses this for its poll loop;
        others noop."""
        return None

    def attach_hint(self) -> str:
        from murder.tmux import attach_command

        return attach_command(self.session)
