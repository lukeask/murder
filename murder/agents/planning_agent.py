"""PlanningAgent — per-plan tmux-backed agent.

One planning agent runs per plan. Its tmux session is cwd=.murder/ so the
harness sees plans/, tickets/, notes/ as its workspace. User chat (TUI) and
crow-ASK relays (PlanningHandler) both reach it via send_keys.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import TYPE_CHECKING, Any

from murder.agents.base import Agent, AgentRole, AgentStatus
from murder.harnesses.base import HarnessAdapter
from murder.harnesses.models import HarnessStartSpec
from murder.storage.paths import agents_dir

if TYPE_CHECKING:
    from murder.service.runtime_scope import AgentLifecycleHost as Runtime


class PlanningAgent(Agent):
    role = AgentRole.PLANNER
    ticket_id = None

    def __init__(
        self,
        agent_id: str,
        session: str,
        plan_name: str,
        harness: HarnessAdapter,
        repo_root: Path,
        *,
        startup_model: str | None = None,
        runtime: Runtime,
    ) -> None:
        self.id = agent_id
        self.session = session
        self.plan_name = plan_name
        self.harness = harness
        self.repo_root = Path(repo_root)
        self.startup_model = startup_model
        self.runtime = runtime
        self.status = AgentStatus.IDLE
        # cwd is .murder/, not the repo root: planners work in the project-state dir.
        self._cwd = agents_dir(self.repo_root)
        self.harness_session = harness.attach(session, self._cwd)

    async def start(self, brief: str, ctx: dict[str, Any]) -> None:
        from murder.bus import StatusChangeEvent

        start_result = await self.harness_session.start(
            HarnessStartSpec(cwd=self._cwd, startup_model=self.startup_model)
        )
        if not start_result.ok:
            self.status = AgentStatus.FAILED
            if self.runtime:
                self.runtime.sync_agent(self)
            raise TimeoutError(start_result.message or "planner harness startup failed")

        if brief:
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

    async def stop(self, *, failed: bool = False, kill_session: bool = True) -> None:
        from murder.terminal import tmux

        if kill_session:
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
        await self.harness_session.send_prompt(msg)
