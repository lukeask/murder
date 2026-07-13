"""PlanningHandler installs ASK routing before asynchronous planner delivery."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from murder.config import PlannerConfig
from murder.llm.harnesses.claude_code import ClaudeCodeAdapter
from murder.llm.harnesses.results import fail_result, ok_result
from murder.runtime.agents.planning_handler import PlanningHandler


def _handler(planner: object, crow: object | None = None) -> PlanningHandler:
    runtime = SimpleNamespace(
        get_agent=lambda _agent_id: planner,
        get_crow=lambda _ticket_id: crow,
    )
    handler = PlanningHandler(
        agent_id="planning_handler-plan",
        session="handler-plan",
        planner_session="planner-plan",
        plan_name="plan",
        harness=ClaudeCodeAdapter(),
        config=PlannerConfig(),
        repo_root=Path("/tmp"),
        runtime=runtime,
    )
    handler._scan_carve_forms = AsyncMock()
    return handler


def _answer_frame(ticket_id: str, answer: str) -> object:
    return SimpleNamespace(
        frame=SimpleNamespace(raw_text="planner pane"),
        evidence=(
            SimpleNamespace(
                payload={
                    "transcript": {
                        "segments": [
                            {
                                "type": "assistant",
                                "text": f">>> ANSWER[{ticket_id}]: {answer}",
                            }
                        ]
                    }
                }
            ),
        ),
    )


@pytest.mark.asyncio
async def test_answer_arriving_during_planner_send_is_routed() -> None:
    """The send await must not leave a window with no pending ASK entry."""
    crow = SimpleNamespace(send=AsyncMock(return_value=ok_result()))
    planner = SimpleNamespace(latest_ingested_frame=_answer_frame("T-1", "use pytest"))
    handler = _handler(planner, crow)

    async def send(_body: str):
        await handler.tick()
        return ok_result()

    planner.send = send

    await handler.relay_ask("T-1", "How should I test this?", "crow-T-1")

    crow.send.assert_awaited_once_with("use pytest")
    assert "T-1" not in handler._pending
    assert "T-1" in handler._routed


@pytest.mark.asyncio
async def test_failed_first_delivery_removes_its_unroutable_registration() -> None:
    planner = SimpleNamespace(send=AsyncMock(return_value=fail_result("tmux gone")))
    handler = _handler(planner)

    with pytest.raises(RuntimeError, match="tmux gone"):
        await handler.relay_ask("T-1", "question", "crow-T-1")

    assert "T-1" not in handler._pending
    assert "T-1" not in handler._routed


@pytest.mark.asyncio
async def test_failed_replacement_restores_the_previously_delivered_question() -> None:
    planner = SimpleNamespace(send=AsyncMock(side_effect=[ok_result(), fail_result("nope")]))
    handler = _handler(planner)

    await handler.relay_ask("T-1", "first", "crow-first")
    first = handler._pending["T-1"]
    with pytest.raises(RuntimeError, match="nope"):
        await handler.relay_ask("T-1", "replacement", "crow-replacement")

    assert handler._pending["T-1"] == first


@pytest.mark.asyncio
async def test_stale_failed_delivery_cleanup_cannot_delete_a_newer_request() -> None:
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    calls = 0

    async def send(_body: str):
        nonlocal calls
        calls += 1
        if calls == 1:
            first_started.set()
            await release_first.wait()
            return fail_result("first send failed")
        return ok_result()

    planner = SimpleNamespace(send=send)
    handler = _handler(planner)

    first = asyncio.create_task(handler.relay_ask("T-1", "first", "crow-first"))
    await first_started.wait()
    await handler.relay_ask("T-1", "replacement", "crow-replacement")
    replacement = handler._pending["T-1"]
    release_first.set()

    with pytest.raises(RuntimeError, match="first send failed"):
        await first

    assert handler._pending["T-1"] == replacement
    assert replacement.ask == "replacement"
