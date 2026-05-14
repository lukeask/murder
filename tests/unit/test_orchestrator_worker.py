from __future__ import annotations

from pathlib import Path

import pytest

from murder.bus.protocol import CommandEvent
from murder.workers.base import WorkerCtx
from murder.workers.orchestrator_worker import OrchestratorCommandWorker


class _FakeNotetaker:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def reply_to(self, text: str) -> str:
        self.messages.append(text)
        return f"ack:{text}"


@pytest.mark.asyncio
async def test_kickoff_ready_command() -> None:
    calls: list[str | None] = []

    async def _kickoff_ready(only: str | None) -> list[str]:
        calls.append(only)
        return ["t001"]

    async def _ensure_notetaker() -> str:
        return "notetaker-0"

    worker = OrchestratorCommandWorker(
        kickoff_ready=_kickoff_ready,
        ensure_notetaker=_ensure_notetaker,
        get_agent=lambda _agent_id: _FakeNotetaker(),
    )
    result = await worker.on_command(
        CommandEvent(
            run_id="r1",
            target_worker="orchestrator",
            kind="scheduler.kickoff_ready",
            payload={},
            correlation_id="corr-1",
            idempotency_key="idem-1",
        ),
        WorkerCtx(repo_root=Path(".")),
    )

    assert calls == [None]
    assert result == {"handled": True, "kicked": ["t001"]}


@pytest.mark.asyncio
async def test_notetaker_chat_send_command() -> None:
    agent = _FakeNotetaker()

    async def _kickoff_ready(_only: str | None) -> list[str]:  # pragma: no cover
        raise AssertionError("should not be called")

    async def _ensure_notetaker() -> str:
        return "notetaker-0"

    worker = OrchestratorCommandWorker(
        kickoff_ready=_kickoff_ready,
        ensure_notetaker=_ensure_notetaker,
        get_agent=lambda agent_id: agent if agent_id == "notetaker-0" else None,
    )
    result = await worker.on_command(
        CommandEvent(
            run_id="r1",
            target_worker="orchestrator",
            kind="notetaker.chat.send",
            payload={"text": "hello"},
            correlation_id="corr-1",
            idempotency_key="idem-1",
        ),
        WorkerCtx(repo_root=Path(".")),
    )

    assert agent.messages == ["hello"]
    assert result == {"handled": True, "agent_id": "notetaker-0", "reply": "ack:hello"}
