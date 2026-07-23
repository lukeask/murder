"""CrowHandler tick retry budget — only fail the ticket after N consecutive failures."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from murder.runtime.agents.crow_handler import CrowHandler, TICK_FAILURE_BUDGET
from murder.config import CrowHandlerConfig
from murder.llm.harnesses.claude_code import ClaudeCodeAdapter
from murder.runtime.orchestration.outcome import TicketOutcomeService
from tests.unit.test_harness_adapters import CC_IDLE


@pytest.fixture
def handler(fake_tmux, tmp_path: Path) -> CrowHandler:
    runtime = MagicMock()
    runtime.db = MagicMock()
    runtime.orchestration_events = MagicMock()
    runtime.orchestration_events.publish = AsyncMock()
    runtime.run_id = "test-run"
    runtime.sync_agent = MagicMock()
    runtime.publish_snapshot = AsyncMock()
    outcome = MagicMock(spec=TicketOutcomeService)
    outcome.fail_ticket = AsyncMock()
    coordinator = MagicMock()
    return CrowHandler(
        agent_id="crow_handler-t001",
        ticket_id="t001",
        session="handler-log",
        crow_session="crow-t001",
        harness=ClaudeCodeAdapter(),
        config=CrowHandlerConfig(model="test", poll_interval_s=999.0),
        repo_root=tmp_path,
        runtime=runtime,
        outcome=outcome,
        coordinator=coordinator,
    )


def test_fresh_instance_has_zero_consecutive_failures(handler):
    assert handler._consecutive_tick_failures == 0
    assert handler._terminal_failure is False


def test_transient_failures_below_budget_do_not_fail_ticket(handler):
    # First two failures (budget == 3) are transient: no terminal, no fail_ticket.
    for _ in range(TICK_FAILURE_BUDGET - 1):
        asyncio.run(handler._handle_tick_failure(RuntimeError("blip")))

    assert handler._terminal_failure is False
    assert handler._consecutive_tick_failures == TICK_FAILURE_BUDGET - 1
    handler.outcome.fail_ticket.assert_not_called()
    handler.runtime.orchestration_events.publish.assert_not_called()


def test_reaching_budget_goes_terminal_and_fails_ticket_once(handler):
    for _ in range(TICK_FAILURE_BUDGET):
        asyncio.run(handler._handle_tick_failure(RuntimeError("blip")))

    assert handler._terminal_failure is True
    handler.outcome.fail_ticket.assert_called_once()
    # Further calls are guarded by the early-return on _terminal_failure.
    asyncio.run(handler._handle_tick_failure(RuntimeError("more")))
    handler.outcome.fail_ticket.assert_called_once()


def test_success_tick_in_loop_resets_consecutive_failures(handler):
    from murder.runtime.agents.base import AgentStatus

    # Accrue some transient failures below budget.
    asyncio.run(handler._handle_tick_failure(RuntimeError("blip")))
    asyncio.run(handler._handle_tick_failure(RuntimeError("blip")))
    assert handler._consecutive_tick_failures == TICK_FAILURE_BUDGET - 1

    # Drive the real _loop for exactly one successful tick: the tick stub flips
    # status so the loop exits after the success-path reset runs.
    handler.status = AgentStatus.RUNNING

    async def _good_tick() -> None:
        handler.status = AgentStatus.DONE

    handler.tick = _good_tick  # type: ignore[method-assign]
    asyncio.run(handler._loop())

    assert handler._consecutive_tick_failures == 0
    assert handler._terminal_failure is False
