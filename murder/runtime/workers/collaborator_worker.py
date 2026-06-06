from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from murder.bus.protocol import CommandEvent
from murder.runtime.workers.base import Worker, WorkerCommand, WorkerCtx, WorkerSpec

EnsureCollaborator = Callable[[], Awaitable[str]]
GetAgent = Callable[[str], Any | None]
SwapModel = Callable[[dict[str, Any], WorkerCtx], Awaitable[dict[str, Any] | None]]


class CollaboratorWorker(Worker):
    def __init__(
        self,
        *,
        ensure_collaborator: EnsureCollaborator,
        get_agent: GetAgent,
        swap_model: SwapModel | None = None,
    ) -> None:
        super().__init__(
            WorkerSpec(
                name="collaborator",
                process_model="subprocess",
                accepts=(
                    "collaborator.chat_send",
                    "collaborator.swap_model",
                    "collaborator.transcript.refresh",
                ),
            )
        )
        self._ensure_collaborator = ensure_collaborator
        self._get_agent = get_agent
        self._swap_model = swap_model

    async def run(self, ctx: WorkerCtx, stop_event: asyncio.Event) -> None:  # noqa: ARG002
        await stop_event.wait()

    async def handle_command(self, command: WorkerCommand, ctx: WorkerCtx) -> bool:
        result = await self._dispatch(command.name, command.args, ctx)
        return bool(result.get("handled"))

    async def on_command(self, command: CommandEvent, ctx: WorkerCtx) -> dict[str, Any]:
        return await self._dispatch(command.kind, command.payload, ctx)

    async def _dispatch(self, kind: str, payload: dict[str, Any], ctx: WorkerCtx) -> dict[str, Any]:
        if kind == "collaborator.chat_send":
            text = payload.get("text")
            if not isinstance(text, str) or not text.strip():
                raise ValueError("collaborator.chat_send requires non-empty payload.text")
            agent_id = await self._ensure_collaborator()
            agent = self._get_agent(agent_id)
            if agent is None:
                raise RuntimeError(f"collaborator agent not found after ensure: {agent_id}")
            send_result = await agent.send(text)
            if send_result is not None and getattr(send_result, "ok", True) is False:
                message = (
                    getattr(send_result, "message", None)
                    or "collaborator message delivery failed"
                )
                if hasattr(agent, "record_notice_block_event"):
                    await agent.record_notice_block_event(
                        f"Collaborator message delivery failed: {message}",
                        severity="error",
                    )
                raise RuntimeError(message)
            # Ground truth: record the exact text the user sent as an authoritative
            # user block once the harness accepts delivery.
            if hasattr(agent, "record_user_block_event"):
                await agent.record_user_block_event(text)
            else:
                agent.record_user_block(text)
            return {"handled": True, "agent_id": agent_id}
        if kind == "collaborator.swap_model":
            if self._swap_model is None:
                return {"handled": True, "ok": False, "error": "not_implemented"}
            result = await self._swap_model(payload, ctx)
            return {"handled": True, **(result or {})}
        if kind == "collaborator.transcript.refresh":
            agent_id = payload.get("agent_id", "collaborator-0")
            agent = self._get_agent(str(agent_id))
            if agent is None or not hasattr(agent, "refresh_transcript"):
                return {"handled": True, "available": False, "turns": []}
            turns = await agent.refresh_transcript()
            return {
                "handled": True,
                "available": True,
                "turns": [{"role": role, "text": text} for role, text in turns],
                "has_parser": agent.harness.has_transcript_parser(),
                "harness_kind": str(agent.harness.kind),
                "session": str(agent.session),
            }
        return {"handled": False}
