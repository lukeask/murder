from __future__ import annotations

from pathlib import Path

import pytest

from murder.bus.protocol import CommandEvent, Role
from murder.workers.base import WorkerCommand, WorkerCtx
from murder.workers.collaborator_worker import CollaboratorWorker


class _FakeAgent:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.harness = type(
            "Harness",
            (),
            {"kind": "codex", "transcript_prompt_markers": ["user", "assistant"]},
        )()
        self.session = "collaborator-session"
        self.turns: list[tuple[str, str]] = [("you", "hi"), ("assistant", "hello")]

    async def send(self, msg: str) -> None:
        self.messages.append(msg)

    async def refresh_transcript(self) -> list[tuple[str, str]]:
        return self.turns


@pytest.mark.asyncio
async def test_chat_send_ensures_and_sends() -> None:
    calls: list[str] = []
    agent = _FakeAgent()

    async def ensure_collaborator() -> str:
        calls.append("ensure")
        return "collaborator-0"

    def get_agent(agent_id: str) -> _FakeAgent | None:
        calls.append(f"get:{agent_id}")
        return agent if agent_id == "collaborator-0" else None

    worker = CollaboratorWorker(
        ensure_collaborator=ensure_collaborator,
        get_agent=get_agent,
    )
    ctx = WorkerCtx(repo_root=Path("."))

    handled = await worker.handle_command(
        WorkerCommand("collaborator.chat_send", {"text": "hello"}),
        ctx,
    )

    assert handled is True
    assert calls == ["ensure", "get:collaborator-0"]
    assert agent.messages == ["hello"]
    assert worker.spec.process_model == "subprocess"
    assert worker.spec.accepts == (
        "collaborator.chat_send",
        "collaborator.swap_model",
        "collaborator.transcript.refresh",
    )


@pytest.mark.asyncio
async def test_chat_send_raises_when_agent_missing() -> None:
    async def ensure_collaborator() -> str:
        return "collaborator-0"

    worker = CollaboratorWorker(
        ensure_collaborator=ensure_collaborator,
        get_agent=lambda _agent_id: None,
    )

    with pytest.raises(RuntimeError, match="collaborator agent not found"):
        await worker.handle_command(
            WorkerCommand("collaborator.chat_send", {"text": "hello"}),
            WorkerCtx(repo_root=Path(".")),
        )


@pytest.mark.asyncio
async def test_swap_model_returns_not_implemented_by_default() -> None:
    async def ensure_collaborator() -> str:
        return "collaborator-0"

    worker = CollaboratorWorker(
        ensure_collaborator=ensure_collaborator,
        get_agent=lambda _agent_id: _FakeAgent(),
    )
    ctx = WorkerCtx(repo_root=Path("."))
    event = CommandEvent(
        run_id="r1",
        agent_id="agent-1",
        role=Role.COLLABORATOR,
        target_worker="collaborator",
        kind="collaborator.swap_model",
        payload={"model": "x"},
        correlation_id="corr-1",
        idempotency_key="idem-1",
    )

    result = await worker.on_command(event, ctx)

    assert result == {"handled": True, "ok": False, "error": "not_implemented"}


@pytest.mark.asyncio
async def test_transcript_refresh_returns_turns_and_metadata() -> None:
    agent = _FakeAgent()
    worker = CollaboratorWorker(
        ensure_collaborator=lambda: _never_called(),
        get_agent=lambda _agent_id: agent,
    )
    ctx = WorkerCtx(repo_root=Path("."))
    event = CommandEvent(
        run_id="r1",
        target_worker="collaborator",
        kind="collaborator.transcript.refresh",
        correlation_id="corr-1",
        idempotency_key="idem-1",
    )

    result = await worker.on_command(event, ctx)

    assert result == {
        "handled": True,
        "available": True,
        "turns": [{"role": "you", "text": "hi"}, {"role": "assistant", "text": "hello"}],
        "has_parser": True,
        "harness_kind": "codex",
        "session": "collaborator-session",
    }


@pytest.mark.asyncio
async def test_transcript_refresh_unavailable_without_agent() -> None:
    worker = CollaboratorWorker(
        ensure_collaborator=lambda: _never_called(),
        get_agent=lambda _agent_id: None,
    )
    ctx = WorkerCtx(repo_root=Path("."))
    event = CommandEvent(
        run_id="r1",
        target_worker="collaborator",
        kind="collaborator.transcript.refresh",
        correlation_id="corr-1",
        idempotency_key="idem-1",
    )

    result = await worker.on_command(event, ctx)

    assert result == {"handled": True, "available": False, "turns": []}


async def _never_called() -> str:
    raise AssertionError("ensure_collaborator should not be called")
