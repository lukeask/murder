"""CollaboratorAgent — wraps Claude Code in a tmux session."""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import TYPE_CHECKING, Any

from murder.agents.base import Agent, AgentRole, AgentStatus
from murder.harnesses.base import HarnessAdapter

if TYPE_CHECKING:
    from murder.runtime import Runtime


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
        runtime: "Runtime | None" = None,
    ) -> None:
        self.id = agent_id
        self.session = session
        self.harness = harness
        self.repo_root = Path(repo_root)
        self.runtime = runtime
        self.status = AgentStatus.IDLE

    async def start(self, brief: str, ctx: dict[str, Any]) -> None:
        from murder import tmux
        from murder.bus import StatusChangeEvent

        await tmux.create_session(
            self.session,
            self.repo_root,
            self.harness.startup_cmd(self.repo_root),
        )
        for _ in range(600):
            pane = await tmux.capture_pane(self.session, lines=80)
            if self.harness.is_ready(pane):
                break
            await asyncio.sleep(0.4)
        else:
            raise TimeoutError(f"Collaborator harness not ready: {self.session}")
        await self.harness.send_prompt(self.session, brief)
        self.status = AgentStatus.RUNNING
        if self.runtime:
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

    async def stop(self) -> None:
        from murder import tmux

        with contextlib.suppress(Exception):
            await self.harness.interrupt(self.session)
        with contextlib.suppress(Exception):
            await tmux.kill_session(self.session)
        self.status = AgentStatus.DONE
        if self.runtime:
            self.runtime.sync_agent(self)

    async def send(self, msg: str) -> None:
        from murder import tmux

        await tmux.send_keys(self.session, msg, literal=True, enter=True)
