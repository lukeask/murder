"""Crow chat queue driven by verified prompt control.

The queue still decides *when* a message may be submitted.  It no longer owns
terminal delivery: each send is a persisted operation whose Enter effect must
be followed by fresh harness evidence before the queue considers it delivered.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from murder.runtime.orchestration.events import ConversationStateEvent
from murder.llm.harness_control.runtime.prompt_driver import PromptDriverPolicy
from murder.llm.harnesses.claude_code import ClaudeCodeAdapter
from murder.runtime.agents.crow import CrowAgent
from murder.state.persistence.schema import get_db, init_db
from tests.unit.test_harness_adapters import CC_BUSY, CC_IDLE

SESSION = "crow_cc_rogue_test"
MINIMUM_VERIFIED_ACTIONS = 2
MINIMUM_EVIDENCE_RECORDS = 3


@pytest.fixture
def agent(fake_tmux, tmp_path: Path) -> CrowAgent:
    connection = get_db(tmp_path / "state.db")
    init_db(connection)

    async def no_sleep(_: float) -> None:
        return None

    runtime = SimpleNamespace()
    runtime.db = connection
    runtime.bus = MagicMock()
    runtime.bus.publish = AsyncMock()
    runtime.run_id = "test-run"
    runtime.sync_agent = MagicMock()
    runtime.verified_prompt_driver_policy = PromptDriverPolicy(
        observation_interval=timedelta(), maximum_observations=12
    )
    runtime.verified_prompt_driver_sleep = no_sleep
    return CrowAgent(
        agent_id="cc-rogue-test",
        ticket_id=None,
        session=SESSION,
        harness=ClaudeCodeAdapter(),
        repo_root=tmp_path,
        runtime=runtime,
    )


def _visible_composer(text: str) -> str:
    return CC_IDLE.replace('❯\xa0Try "create a util logging.py that..."', f"❯ {text}")


async def _prepare_verified_delivery(agent: CrowAgent, fake_tmux, text: str) -> None:
    """Script insertion and a later acknowledgment, never a send result."""

    await agent.initialize_verified_harness_control()
    fake_tmux.queue_pane(CC_IDLE)
    fake_tmux.queue_pane_after_effect(
        _visible_composer(text), effect="paste_buffer_literal", effect_text=text
    )
    fake_tmux.queue_pane_after_effect(CC_IDLE, effect="send_keys", effect_text="Enter")


def _assert_verified_submission(agent: CrowAgent, fake_tmux) -> None:
    """The behavioral assertion is operation/evidence convergence, not tmux I/O."""

    connection = agent.runtime.db
    assert connection.execute("SELECT COUNT(*) FROM harness_control_operations").fetchone()[0] == 1
    assert (
        connection.execute("SELECT COUNT(*) FROM harness_control_actions").fetchone()[0]
        >= MINIMUM_VERIFIED_ACTIONS
    )
    assert (
        connection.execute("SELECT COUNT(*) FROM harness_control_evidence").fetchone()[0]
        >= MINIMUM_EVIDENCE_RECORDS
    )
    enters = [args for args, _ in fake_tmux.calls_to("send_keys") if args[1] == "Enter"]
    assert len(enters) == 1


def _published_state_events(agent: CrowAgent) -> list[ConversationStateEvent]:
    return [
        call.args[0]
        for call in agent.runtime.bus.publish.await_args_list
        if isinstance(call.args[0], ConversationStateEvent)
    ]


def test_queue_message_holds_when_pane_busy(agent, fake_tmux):
    fake_tmux.queue_pane(CC_BUSY)
    result = asyncio.run(agent.queue_message("wait for idle"))
    assert result == {"queued": True}
    assert agent.pending_message == "wait for idle"
    assert fake_tmux.calls_to("send_keys") == []
    # The queued message is pushed to clients as a conversation.state event.
    events = _published_state_events(agent)
    assert events and events[-1].queued_message == "wait for idle"


def test_second_queued_message_appends(agent, fake_tmux):
    fake_tmux.queue_pane(CC_BUSY)
    asyncio.run(agent.queue_message("first"))
    asyncio.run(agent.queue_message("second"))
    assert agent.pending_message == "first\n\nsecond"


def test_queued_message_delivered_when_parser_reports_awaiting_input(agent, fake_tmux):
    fake_tmux.queue_pane(CC_BUSY)
    asyncio.run(agent.queue_message("held"))
    assert agent.pending_message == "held"

    # Simulate the projection tick observing an input-ready parse.
    agent._producer = MagicMock()
    agent._producer.last_state = "awaiting_input"
    asyncio.run(_prepare_verified_delivery(agent, fake_tmux, "held"))
    asyncio.run(agent._deliver_queued_if_ready())

    assert agent.pending_message is None
    _assert_verified_submission(agent, fake_tmux)
    # Cleared queue is pushed too (queued_message None, live_state awaiting_input).
    events = _published_state_events(agent)
    assert events[-1].queued_message is None
    assert events[-1].live_state == "awaiting_input"


def test_queued_message_not_delivered_during_choice_prompt(agent, fake_tmux):
    fake_tmux.queue_pane(CC_BUSY)
    asyncio.run(agent.queue_message("held"))

    agent._producer = MagicMock()
    agent._producer.last_state = "awaiting_approval"
    asyncio.run(agent._deliver_queued_if_ready())

    assert agent.pending_message == "held"
    assert fake_tmux.calls_to("send_keys") == []


def test_queue_message_prefers_parser_state_over_pane_heuristic(agent, fake_tmux):
    # Codex keeps its input box visible while working, so the adapter's
    # is_idle pane heuristic can say "idle" mid-task. The parser's live state
    # ("working") must win — the message queues instead of typing into the
    # busy pane.
    fake_tmux.queue_pane(CC_IDLE)
    agent._producer = MagicMock()
    agent._producer.last_state = "working"
    result = asyncio.run(agent.queue_message("must queue"))
    assert result == {"queued": True}
    assert agent.pending_message == "must queue"
    assert fake_tmux.calls_to("send_keys") == []


def test_queue_message_sends_now_when_parser_reports_awaiting_input(agent, fake_tmux):
    # With parsed input readiness the queue may start a semantic operation
    # immediately, but verified control still captures evidence before/after
    # physical effects.
    agent._producer = MagicMock()
    agent._producer.last_state = "awaiting_input"
    asyncio.run(_prepare_verified_delivery(agent, fake_tmux, "send now"))
    result = asyncio.run(agent.queue_message("send now"))
    assert result == {"queued": False}
    assert fake_tmux.calls_to("capture_pane")
    _assert_verified_submission(agent, fake_tmux)
