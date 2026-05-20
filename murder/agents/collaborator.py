"""CollaboratorAgent — wraps an interactive coding CLI (harness) in a tmux
session for the "collaborator" planning mode."""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import TYPE_CHECKING, Any

from murder import conversation, tmux
from murder.agents.base import Agent, AgentRole, AgentStatus
from murder.harnesses.base import HarnessAdapter
from murder.harnesses.models import HarnessStartSpec

# How far back into the pane's scrollback to read when reconstructing the chat
# transcript. Generous: a planning conversation should fit comfortably.
TRANSCRIPT_SCROLLBACK_LINES = 4000

if TYPE_CHECKING:
    from murder.runtime import Runtime

# Keep the harness's own ready/idle waits comfortably under the TUI's hard
# spawn timeout (`MurderApp.COLLABORATOR_START_TIMEOUT_S`) so a slow harness
# surfaces its own clean failure instead of being cancelled mid-startup.
READY_TIMEOUT_S = 75.0


class CollaboratorAgent(Agent):
    role = AgentRole.COLLABORATOR
    ticket_id = None

    def __init__(
        self,
        agent_id: str,
        session: str,
        harness: HarnessAdapter,
        repo_root: Path,
        *,
        startup_model: str | None = None,
        runtime: Runtime | None = None,
    ) -> None:
        self.id = agent_id
        self.session = session
        self.harness = harness
        self.repo_root = Path(repo_root)
        self.startup_model = startup_model
        self.runtime = runtime
        self.status = AgentStatus.IDLE
        self.harness_session = harness.attach(session, self.repo_root)

    async def start(self, brief: str, ctx: dict[str, Any]) -> None:
        from murder.bus import StatusChangeEvent

        start_result = await self.harness_session.start(
            HarnessStartSpec(
                cwd=self.repo_root,
                startup_model=self.startup_model,
                ready_timeout_s=READY_TIMEOUT_S,
            )
        )
        if not start_result.ok:
            raise TimeoutError(start_result.message or "collaborator startup failed")
        await self.harness_session.send_prompt(brief)
        # If the harness binary launched but then exited (e.g. an unanswered
        # interactive prompt, a crash, or a missing/broken install), the tmux
        # session is already gone — say so plainly instead of leaving the pane
        # mirror to report a bare "[session vanished]".
        if not await tmux.session_exists(self.session):
            raise RuntimeError(
                f"collaborator harness '{self.harness.kind}' exited right after startup; "
                "check it runs interactively in this repo (`murder doctor`)"
            )
        self.status = AgentStatus.RUNNING
        if self.runtime:
            if self.runtime.db is not None:
                # Fresh tmux session ⇒ fresh transcript; don't surface a prior
                # run's chat in the new one.
                conversation.clear(self.runtime.db, self.id)
            self.runtime.sync_agent(self)
            if self.runtime.bus and self.runtime.run_id:
                await self.runtime.bus.publish(
                    StatusChangeEvent(
                        run_id=self.runtime.run_id,
                        agent_id=self.id,
                        role=self.role,
                        ticket_id=None,
                        entity="agent",
                        entity_id=self.id,
                        from_status=AgentStatus.IDLE.value,
                        to_status=AgentStatus.RUNNING.value,
                    )
                )

    async def is_live(self) -> bool:
        return await tmux.session_exists(self.session)

    async def stop(self, *, failed: bool = False, kill_session: bool = True) -> None:
        if kill_session:
            with contextlib.suppress(Exception):
                await self.harness_session.interrupt()
            with contextlib.suppress(Exception):
                await tmux.kill_session(self.session)
        self.status = AgentStatus.FAILED if failed else AgentStatus.DONE
        if self.runtime:
            self.runtime.sync_agent(self)

    async def send(self, msg: str) -> None:
        await self.harness_session.send_prompt(msg)

    async def refresh_transcript(self) -> list[tuple[str, str]]:
        """Capture the session pane, parse it with the harness adapter, merge
        into the persisted conversation log, and return the effective
        transcript as ``(role, text)`` turns (``role`` ∈ ``{"user","assistant"}``).

        Returns ``[]`` if the session is gone or the harness has no transcript
        parser yet (the TUI falls back to the raw pane mirror in that case).
        """
        try:
            pane = await tmux.capture_pane(self.session, lines=TRANSCRIPT_SCROLLBACK_LINES)
        except tmux.TmuxError:
            return []
        parsed = self.harness.parse_transcript(pane)
        if self.runtime is None or self.runtime.db is None:
            return parsed
        return conversation.merge_transcript(self.runtime.db, self.id, parsed)
