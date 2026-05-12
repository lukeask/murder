"""CrowAgent — wraps an interactive coding CLI in a tmux session."""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import TYPE_CHECKING, Any

from murder.agents.base import Agent, AgentRole, AgentStatus
from murder.harnesses.base import HarnessAdapter
from murder.harnesses.models import HarnessStartSpec

if TYPE_CHECKING:
    from murder.runtime import Runtime


class CrowAgent(Agent):
    role = AgentRole.CROW

    def __init__(
        self,
        agent_id: str,
        ticket_id: str,
        session: str,
        harness: HarnessAdapter,
        repo_root: Path,
        *,
        startup_model: str | None = None,
        runtime: Runtime | None = None,
    ) -> None:
        self.id = agent_id
        self.ticket_id = ticket_id
        self.session = session
        self.harness = harness
        self.repo_root = Path(repo_root)
        self.startup_model = startup_model
        self.runtime = runtime
        self.status = AgentStatus.IDLE
        self.start_commit: str | None = None
        self.harness_session = harness.attach(session, self.repo_root)

    async def start(self, brief: str, ctx: dict[str, Any]) -> None:
        from murder.bus import ErrorEvent, StatusChangeEvent
        from murder.enforcement import git_diff

        start_result = await self.harness_session.start(
            HarnessStartSpec(cwd=self.repo_root, startup_model=self.startup_model)
        )
        if not start_result.ok:
            self.status = AgentStatus.FAILED
            if self.runtime:
                self.runtime.sync_agent(self)
            raise TimeoutError(start_result.message or "harness startup failed")

        try:
            self.start_commit = await git_diff.head_commit(self.repo_root)
        except Exception as e:
            self.status = AgentStatus.FAILED
            if self.runtime:
                self.runtime.sync_agent(self)
                if self.runtime.bus and self.runtime.run_id:
                    await self.runtime.bus.publish(
                        ErrorEvent(
                            run_id=self.runtime.run_id,
                            agent_id=self.id,
                            role=self.role,
                            ticket_id=self.ticket_id,
                            message=str(e),
                            recoverable=True,
                        )
                    )
            raise
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
                        ticket_id=self.ticket_id,
                        entity="agent",
                        entity_id=self.id,
                        from_status=AgentStatus.IDLE.value,
                        to_status=AgentStatus.RUNNING.value,
                    )
                )

    async def stop(self, *, failed: bool = False) -> None:
        from murder import tmux

        with contextlib.suppress(Exception):
            await self.harness_session.interrupt()
        with contextlib.suppress(Exception):
            await tmux.kill_session(self.session)
        if failed or self.status == AgentStatus.FAILED:
            self.status = AgentStatus.FAILED
        else:
            self.status = AgentStatus.DONE
        if self.runtime:
            self.runtime.sync_agent(self)

    async def send(self, msg: str) -> None:
        text = self.harness.format_nudge(msg)
        await self.harness_session.send_prompt(text)
