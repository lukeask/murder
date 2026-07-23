"""PlanningAgent — per-plan tmux-backed agent.

One planning agent runs per plan. Its tmux session is cwd=.murder/ so the
harness sees plans/, tickets/, notes/ as its workspace. User chat (TUI) and
crow-ASK relays (PlanningHandler) both reach it via send_keys.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import TYPE_CHECKING, Any

from murder.runtime.agents.base import HarnessBackedAgent, AgentRole, AgentStatus
from murder.llm.harnesses.base import HarnessAdapter
from murder.llm.harnesses.models import HarnessStartSpec
from murder.llm.harnesses.results import SimpleResult
from murder.state.storage.paths import agents_dir

if TYPE_CHECKING:
    from murder.app.service.runtime_scope import AgentLifecycleHost as Runtime


class PlanningAgent(HarnessBackedAgent):
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
        startup_effort: str | None = None,
        runtime: Runtime,
    ) -> None:
        self.id = agent_id
        self.session = session
        self.plan_name = plan_name
        self.harness = harness
        self.repo_root = Path(repo_root)
        self.startup_model = startup_model
        self.startup_effort = startup_effort
        self.runtime = runtime
        self.status = AgentStatus.IDLE
        # cwd is .murder/, not the repo root: planners work in the project-state dir.
        self._cwd = agents_dir(self.repo_root)
        self.harness_session = harness.attach(session, self._cwd)

    async def start(self, brief: str, ctx: dict[str, Any]) -> None:
        from murder.runtime.orchestration.events import StatusChangeEvent

        # Record the injected system prompt so the transcript parser can drop it
        # rather than mislabel its paragraphs as chat turns (markerless harnesses).
        self.harness.system_prompt = brief
        start_result = await self.harness_session.start(
            HarnessStartSpec(
                cwd=self._cwd,
                startup_model=self.startup_model,
                startup_effort=self.startup_effort,
            )
        )
        if not start_result.ok:
            self.status = AgentStatus.FAILED
            if self.runtime:
                self.runtime.sync_agent(self)
            raise TimeoutError(start_result.message or "planner harness startup failed")

        await self.initialize_verified_harness_control()
        model_result = await self.select_verified_model(self.startup_model, self.startup_effort)
        if not model_result.ok:
            self.status = AgentStatus.FAILED
            if self.runtime:
                self.runtime.sync_agent(self)
            raise RuntimeError(model_result.message or "verified planner model selection failed")

        await self._sample_live_usage_on_startup()

        if brief:
            send_result = await self.send_verified_prompt(brief, murder_owned=True)
            if not send_result.ok:
                self.status = AgentStatus.FAILED
                if self.runtime:
                    self.runtime.sync_agent(self)
                raise RuntimeError(send_result.message or "planner startup prompt failed")
        self.status = AgentStatus.RUNNING
        # Fresh tmux session: fresh transcript and producer-owned parser state.
        self.start_conversation()
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
        from murder.runtime.terminal import tmux

        if kill_session and not failed:
            await self._sample_live_usage_on_shutdown()
        if kill_session:
            with contextlib.suppress(Exception):
                await self.interrupt_verified_generation()
            terminated = await self.terminate_verified_session(force=failed)
            control = self.verified_harness_control
            if not terminated and (
                control is None or control.session_controller is None
            ):
                # Startup failures may occur before a controller exists.
                with contextlib.suppress(Exception):
                    await tmux.kill_session(self.session)
        if failed or self.status == AgentStatus.FAILED:
            self.status = AgentStatus.FAILED
        else:
            self.status = AgentStatus.DONE
        if self.runtime:
            self.runtime.sync_agent(self)

    async def send(self, msg: str) -> SimpleResult[None]:
        return await self.send_verified_prompt(msg)
