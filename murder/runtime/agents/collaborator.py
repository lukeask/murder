"""CollaboratorAgent — wraps an interactive coding CLI (harness) in a tmux
session for the "collaborator" planning mode."""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import TYPE_CHECKING, Any

from murder.runtime.agents.base import HarnessBackedAgent, AgentRole, AgentStatus
from murder.llm.harnesses.base import HarnessAdapter
from murder.llm.harnesses.models import HarnessStartSpec
from murder.llm.harnesses.results import SimpleResult
from murder.runtime.terminal import tmux

# How far back into the pane's scrollback to read when reconstructing the chat
# transcript. Generous: a planning conversation should fit comfortably.
TRANSCRIPT_SCROLLBACK_LINES = 4000

if TYPE_CHECKING:
    from murder.app.service.runtime_scope import AgentLifecycleHost as Runtime

# Keep the harness's own ready/idle waits comfortably under the service-level
# spawn timeout so a slow harness surfaces its own clean failure instead of being cancelled mid-startup.
READY_TIMEOUT_S = 75.0


class CollaboratorAgent(HarnessBackedAgent):
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
        startup_effort: str | None = None,
        runtime: Runtime | None = None,
    ) -> None:
        self.id = agent_id
        self.session = session
        self.harness = harness
        self.repo_root = Path(repo_root)
        self.startup_model = startup_model
        self.startup_effort = startup_effort
        self.runtime = runtime
        self.status = AgentStatus.IDLE
        self.harness_session = harness.attach(session, self.repo_root)

    async def start(self, brief: str, ctx: dict[str, Any]) -> None:
        from murder.bus import StatusChangeEvent

        # Record the injected system prompt so the (markerless) transcript parser
        # can recognise and drop it instead of mislabelling its paragraphs as
        # alternating user/assistant chat turns.
        self.harness.system_prompt = brief
        # Fresh startup attempt means fresh transcript, even if the harness
        # fails before it can accept the prompt; the failure notice belongs to
        # this attempt, not a prior successful conversation.
        self.start_conversation()
        start_result = await self.harness_session.start(
            HarnessStartSpec(
                cwd=self.repo_root,
                startup_model=self.startup_model,
                startup_effort=self.startup_effort,
                ready_timeout_s=READY_TIMEOUT_S,
            )
        )
        if not start_result.ok:
            await self._fail_startup(start_result.message or "collaborator startup failed")
            raise TimeoutError(start_result.message or "collaborator startup failed")
        send_result = await self.harness_session.send_prompt(brief)
        if not send_result.ok:
            message = send_result.message or "collaborator startup prompt failed"
            await self._fail_startup(message)
            raise RuntimeError(message)
        # The brief send returns as soon as the keystrokes are delivered — the
        # harness is now *working on the brief*, not idle. Re-arm the first-send
        # idle gate so the user's first real message (delivered by the worker
        # right after ensure_collaborator() returns) waits for the pane to come
        # back to input-ready instead of landing in a busy harness, where the
        # text sits unsubmitted and never runs as a turn. This mirrors the Crow
        # path's deliver-only-when-idle guarantee
        # (HarnessBackedAgent.queue_message).
        self.harness_session.require_first_send_idle_gate()
        # If the harness binary launched but then exited (e.g. an unanswered
        # interactive prompt, a crash, or a missing/broken install), the tmux
        # session is already gone — say so plainly instead of leaving the pane
        # mirror to report a bare "[session vanished]".
        if not await tmux.session_exists(self.session):
            message = (
                f"collaborator harness '{self.harness.kind}' exited right after startup; "
                "check it runs interactively in this repo"
            )
            await self._fail_startup(message)
            raise RuntimeError(message)
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

    async def _fail_startup(self, message: str) -> None:
        self.status = AgentStatus.FAILED
        await self.record_notice_block_event(
            f"Collaborator startup failed: {message}",
            severity="error",
        )
        if self.runtime:
            self.runtime.sync_agent(self)

    async def stop(self, *, failed: bool = False, kill_session: bool = True) -> None:
        await self._finalize_conversation_on_stop(kill_session=kill_session, failed=failed)
        if kill_session:
            with contextlib.suppress(Exception):
                await self.harness_session.interrupt()
            with contextlib.suppress(Exception):
                await tmux.kill_session(self.session)
        self.status = AgentStatus.FAILED if failed else AgentStatus.DONE
        if self.runtime:
            self.runtime.sync_agent(self)

    async def send(self, msg: str) -> SimpleResult[None]:
        return await self.harness_session.send_prompt(msg)
