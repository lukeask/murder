"""CrowAgent — wraps an interactive coding CLI in a tmux session."""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import TYPE_CHECKING, Any

from murder.runtime.agents.base import HarnessBackedAgent, AgentRole, AgentStatus
from murder.llm.harnesses.base import HarnessAdapter
from murder.llm.harnesses.models import HarnessStartSpec
from murder.llm.harnesses.results import SimpleResult
from murder.runtime.terminal import tmux

if TYPE_CHECKING:
    from murder.app.service.runtime_scope import AgentLifecycleHost as Runtime


class CrowAgent(HarnessBackedAgent):
    role = AgentRole.CROW

    def __init__(
        self,
        agent_id: str,
        ticket_id: str | None,
        session: str,
        harness: HarnessAdapter,
        repo_root: Path,
        *,
        startup_model: str | None = None,
        startup_effort: str | None = None,
        worktree_path: Path | None = None,
        additional_workspace_dirs: tuple[Path, ...] = (),
        runtime: Runtime | None = None,
    ) -> None:
        self.id = agent_id
        self.ticket_id = ticket_id
        self.session = session
        self.harness = harness
        self.repo_root = Path(repo_root)
        self.worktree_path = Path(worktree_path) if worktree_path is not None else None
        self.additional_workspace_dirs = tuple(
            Path(path) for path in additional_workspace_dirs
        )
        self.startup_model = startup_model
        self.startup_effort = startup_effort
        self.runtime = runtime
        self.status = AgentStatus.IDLE
        self.start_commit: str | None = None
        self.harness_session = harness.attach(session, self.repo_root)

    async def start(self, brief: str, ctx: dict[str, Any]) -> None:
        from murder.bus import StatusChangeEvent
        from murder.verdict.enforcement import git_diff

        # Record the injected system prompt so the transcript parser can drop it
        # rather than mislabel its paragraphs as chat turns (markerless harnesses).
        self.harness.system_prompt = brief
        start_result = await self.harness_session.start(
            HarnessStartSpec(
                cwd=self.repo_root,
                startup_model=self.startup_model,
                startup_effort=self.startup_effort,
                additional_workspace_dirs=self.additional_workspace_dirs,
            )
        )
        if not start_result.ok:
            self.status = AgentStatus.FAILED
            if self.runtime:
                self.runtime.sync_agent(self)
            raise TimeoutError(start_result.message or "harness startup failed")

        try:
            self.start_commit = await git_diff.head_commit(self.repo_root)
        except Exception:
            self.start_commit = None
        paste = await self.harness_session.send_prompt(brief)
        if not paste.ok:
            self.status = AgentStatus.FAILED
            if self.runtime:
                self.runtime.sync_agent(self)
            raise RuntimeError(paste.message or "failed to deliver startup context")
        self.status = AgentStatus.RUNNING
        # Fresh tmux session ⇒ fresh transcript + accumulator scrollback.
        self.start_conversation()
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

    async def stop(self, *, failed: bool = False, kill_session: bool = True) -> None:
        from murder.runtime.terminal import tmux

        await self._finalize_conversation_on_stop(kill_session=kill_session, failed=failed)
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

    async def send(self, msg: str) -> SimpleResult[None]:
        return await self.harness_session.send_prompt(msg)
