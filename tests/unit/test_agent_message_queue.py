"""HarnessBackedAgent busy-harness chat queue (rogues + ticket crows).

Covers the agent-level generalization of the CrowHandler queue: send-now when
the pane is input-ready, hold + DB mirror + ConversationStateEvent when busy,
and delivery from the projection tick once the parser reports awaiting_input.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from murder.bus import ConversationStateEvent
from murder.llm.harnesses.claude_code import ClaudeCodeAdapter
from murder.runtime.agents.crow import CrowAgent
from tests.unit.test_harness_adapters import CC_BUSY, CC_IDLE

SESSION = "crow_cc_rogue_test"


@pytest.fixture
def agent(fake_tmux, tmp_path: Path) -> CrowAgent:
    runtime = MagicMock()
    runtime.db = MagicMock()
    runtime.bus = MagicMock()
    runtime.bus.publish = AsyncMock()
    runtime.run_id = "test-run"
    return CrowAgent(
        agent_id="cc-rogue-test",
        ticket_id=None,
        session=SESSION,
        harness=ClaudeCodeAdapter(),
        repo_root=tmp_path,
        runtime=runtime,
    )


def _published_state_events(agent: CrowAgent) -> list[ConversationStateEvent]:
    return [
        call.args[0]
        for call in agent.runtime.bus.publish.await_args_list
        if isinstance(call.args[0], ConversationStateEvent)
    ]


def test_queue_message_sends_immediately_when_pane_idle(agent, fake_tmux):
    fake_tmux.queue_pane(CC_IDLE)
    result = asyncio.run(agent.queue_message("nudge"))
    assert result == {"queued": False}
    send_calls = fake_tmux.calls_to("send_keys")
    assert len(send_calls) == 1
    (session_arg, text), kw = send_calls[0]
    assert session_arg == SESSION
    assert text == "nudge"
    assert agent.pending_message is None


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
    asyncio.run(agent._deliver_queued_if_ready())

    assert agent.pending_message is None
    send_calls = fake_tmux.calls_to("send_keys")
    assert len(send_calls) == 1
    assert send_calls[0][0][1] == "held"
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
    # With a parsed awaiting_input state the message sends immediately — no
    # pane capture / heuristic involved.
    agent._producer = MagicMock()
    agent._producer.last_state = "awaiting_input"
    result = asyncio.run(agent.queue_message("send now"))
    assert result == {"queued": False}
    assert fake_tmux.calls_to("capture_pane") == []
    send_calls = fake_tmux.calls_to("send_keys")
    assert len(send_calls) == 1
    assert send_calls[0][0][1] == "send now"
