"""Agent ABC + lifecycle.

All roles implement this interface. `CrowAgent`, `CollaboratorAgent`, and
`PlanningAgent` own real tmux sessions (interactive harness) and subclass
`HarnessBackedAgent`. `CrowHandler` and `PlanningHandler` are coroutine
daemons that subclass `Daemon` directly; they own no interactive pane and
have no transcript.
"""

from __future__ import annotations

import asyncio
import contextlib
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

# Re-export from bus to keep StrEnum definitions in one place.
from murder.bus import AgentStatus
from murder.bus import Role as AgentRole

if TYPE_CHECKING:
    from murder.harnesses.base import HarnessAdapter

__all__ = ["LifecycleParticipant", "HarnessBackedAgent", "Daemon", "AgentRole", "AgentStatus"]
TRANSCRIPT_SCROLLBACK_LINES = 4000


class LifecycleParticipant(ABC):
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

        HarnessBackedAgent overrides this to check tmux session existence.
        Daemon subclasses use status. Default is True so non-overriding
        participants are considered live unless explicitly stopped.
        """
        return True

    async def tick(self) -> None:
        """Optional cadence hook. CrowHandler uses this for its poll loop;
        others noop."""
        return None

    def attach_hint(self) -> str:
        from murder.terminal.tmux import attach_command

        return attach_command(self.session)


class HarnessBackedAgent(LifecycleParticipant):
    """Lifecycle participant that owns an interactive harness pane in tmux.

    Provides transcript persistence via refresh_transcript(), shared by
    CollaboratorAgent, PlanningAgent, and CrowAgent.
    """

    harness: HarnessAdapter
    harness_session: Any  # HarnessSession — typed Any to avoid import cycle

    async def is_live(self) -> bool:
        from murder.terminal import tmux

        return await tmux.session_exists(self.session)

    async def refresh_transcript(self) -> list[tuple[str, str]]:
        """Capture the session pane, parse it with the harness adapter, merge
        into the persisted conversation log, and return the effective
        transcript as ``(role, text)`` turns (``role`` ∈ ``{"user","assistant"}``).

        Returns ``[]`` if the session is gone or the harness has no transcript
        parser yet (the TUI falls back to the raw pane mirror in that case).
        """
        from murder.persistence import conversation
        from murder.terminal import tmux

        try:
            pane = await tmux.capture_pane(self.session, lines=TRANSCRIPT_SCROLLBACK_LINES)
        except tmux.TmuxError:
            return []
        parsed = self.harness.parse_transcript(pane)
        runtime = getattr(self, "runtime", None)
        if runtime is None or runtime.db is None:
            return parsed
        return conversation.merge_transcript(runtime.db, self.id, parsed)


class Daemon(LifecycleParticipant):
    """Lifecycle participant that runs a background poll loop."""

    _poll_task: asyncio.Task[None] | None = None

    def _start_loop(self) -> None:
        self._poll_task = asyncio.create_task(self._loop())

    @abstractmethod
    async def _loop(self) -> None: ...

    async def stop(self, *, failed: bool = False, kill_session: bool = True) -> None:
        del failed, kill_session
        if self._poll_task is not None:
            self._poll_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._poll_task
            self._poll_task = None
        if getattr(self, "runtime", None) is not None and self.runtime.db is not None:
            self.runtime.sync_agent(self)

    async def send(self, msg: str) -> None:
        # Daemons do not own a conversation pane; user/crow chat goes to the
        # paired HarnessBackedAgent, not its handler.
        del msg
