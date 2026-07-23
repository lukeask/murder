"""PlanningHandler poll-failure accounting: log every tick, escalate once at threshold."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from murder.runtime.agents.planning_handler import (
    PlanningHandler,
    POLL_FAILURE_ESCALATION_THRESHOLD,
)
from murder.config import PlannerConfig
from murder.llm.harnesses.claude_code import ClaudeCodeAdapter
from murder.runtime.orchestration.events import ErrorEvent


@pytest.fixture
def handler(tmp_path: Path) -> PlanningHandler:
    runtime = MagicMock()
    runtime.orchestration_events = MagicMock()
    runtime.orchestration_events.publish = AsyncMock()
    runtime.run_id = "test-run"
    runtime.sync_agent = MagicMock()
    return PlanningHandler(
        agent_id="planning_handler-planX",
        session="handler-log",
        planner_session="planner-planX",
        plan_name="planX",
        harness=ClaudeCodeAdapter(),
        config=PlannerConfig(),
        repo_root=tmp_path,
        runtime=runtime,
    )


def test_fresh_instance_has_zero_poll_failures(handler):
    assert handler._consecutive_poll_failures == 0


def test_below_threshold_does_not_publish(handler):
    for _ in range(POLL_FAILURE_ESCALATION_THRESHOLD - 1):
        published = asyncio.run(handler._record_poll_failure(RuntimeError("blip")))
        assert published is False

    handler.runtime.orchestration_events.publish.assert_not_called()
    assert handler._consecutive_poll_failures == POLL_FAILURE_ESCALATION_THRESHOLD - 1
    # Handler is not terminated by failures.
    assert handler.status.value != "failed"


def test_threshold_publishes_exactly_one_error_event(handler):
    for _ in range(POLL_FAILURE_ESCALATION_THRESHOLD):
        asyncio.run(handler._record_poll_failure(RuntimeError("blip")))

    handler.runtime.orchestration_events.publish.assert_awaited_once()
    (event,), _kw = handler.runtime.orchestration_events.publish.call_args
    assert isinstance(event, ErrorEvent)
    assert event.recoverable is True
    assert event.ticket_id is None
    assert event.agent_id == handler.id
    assert "planX" in event.message

    # Further failures past the threshold do NOT re-publish.
    asyncio.run(handler._record_poll_failure(RuntimeError("more")))
    handler.runtime.orchestration_events.publish.assert_awaited_once()


def test_success_resets_counter_and_rearms(handler):
    for _ in range(POLL_FAILURE_ESCALATION_THRESHOLD - 1):
        asyncio.run(handler._record_poll_failure(RuntimeError("blip")))
    assert handler._consecutive_poll_failures == POLL_FAILURE_ESCALATION_THRESHOLD - 1

    # A clean tick resets the counter (mirrors the _loop else-branch).
    handler._consecutive_poll_failures = 0

    # Re-armed: it takes a full threshold run again to publish.
    for _ in range(POLL_FAILURE_ESCALATION_THRESHOLD - 1):
        published = asyncio.run(handler._record_poll_failure(RuntimeError("blip")))
        assert published is False
    handler.runtime.orchestration_events.publish.assert_not_called()
