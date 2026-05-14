from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from murder.bus.protocol import CommandEvent
from murder.workers.base import Worker, WorkerCtx, WorkerSpec

KickoffReady = Callable[[str | None], Awaitable[list[str]]]
EnsureNotetaker = Callable[[], Awaitable[str]]
GetAgent = Callable[[str], Any | None]


class OrchestratorCommandWorker(Worker):
    def __init__(
        self,
        *,
        kickoff_ready: KickoffReady,
        ensure_notetaker: EnsureNotetaker,
        get_agent: GetAgent,
    ) -> None:
        super().__init__(
            WorkerSpec(
                name="orchestrator",
                process_model="thread",
                accepts=("scheduler.kickoff_ready", "notetaker.chat.send"),
            )
        )
        self._kickoff_ready = kickoff_ready
        self._ensure_notetaker = ensure_notetaker
        self._get_agent = get_agent

    async def run(self, ctx: WorkerCtx, stop_event: asyncio.Event) -> None:  # noqa: ARG002
        await stop_event.wait()

    async def on_command(self, command: CommandEvent, ctx: WorkerCtx) -> dict[str, Any]:  # noqa: ARG002
        if command.kind == "scheduler.kickoff_ready":
            only = command.payload.get("only")
            if only is not None and not isinstance(only, str):
                raise ValueError("scheduler.kickoff_ready payload.only must be a string or null")
            kicked = await self._kickoff_ready(only)
            return {"handled": True, "kicked": kicked}
        if command.kind == "notetaker.chat.send":
            text = command.payload.get("text")
            if not isinstance(text, str) or not text.strip():
                raise ValueError("notetaker.chat.send requires non-empty payload.text")
            agent_id = await self._ensure_notetaker()
            agent = self._get_agent(agent_id)
            if agent is None:
                raise RuntimeError(f"notetaker agent not found after ensure: {agent_id}")
            reply = await agent.reply_to(text)
            return {"handled": True, "agent_id": agent_id, "reply": reply}
        return {"handled": False}
