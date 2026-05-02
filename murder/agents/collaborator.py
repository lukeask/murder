"""CollaboratorAgent — wraps Claude Code in a tmux session."""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import TYPE_CHECKING, Any

from murder.agents.base import Agent, AgentRole, AgentStatus
from murder.harnesses.base import HarnessAdapter
from murder.harnesses.models import HarnessStartSpec

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
            HarnessStartSpec(cwd=self.repo_root, startup_model=self.startup_model)
        )
        if not start_result.ok:
            raise TimeoutError(start_result.message or "collaborator startup failed")
        await self.harness_session.send_prompt(brief)
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
            await self.harness_session.interrupt()
        with contextlib.suppress(Exception):
            await tmux.kill_session(self.session)
        self.status = AgentStatus.DONE
        if self.runtime:
            self.runtime.sync_agent(self)

    async def send(self, msg: str) -> None:
        await self.harness_session.send_prompt(msg)
