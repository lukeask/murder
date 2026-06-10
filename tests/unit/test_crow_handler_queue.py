"""CrowHandler queue_message and idle flush."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from murder.runtime.agents.crow_handler import CrowHandler
from murder.config import CrowHandlerConfig
from murder.llm.harnesses.results import fail_result
from murder.llm.harnesses.claude_code import ClaudeCodeAdapter
from murder.runtime.orchestration.outcome import TicketOutcomeService
from tests.unit.test_harness_adapters import CC_BUSY, CC_IDLE

SESSION = "crow-t001"


@pytest.fixture
def handler(fake_tmux, tmp_path: Path) -> CrowHandler:
    fake_tmux.queue_pane(CC_BUSY)
    runtime = MagicMock()
    runtime.db = MagicMock()
    runtime.bus = MagicMock()
    runtime.run_id = "test-run"
    runtime.sync_agent = MagicMock()
    runtime.publish_snapshot = AsyncMock()
    outcome = MagicMock(spec=TicketOutcomeService)
    coordinator = MagicMock()
    return CrowHandler(
        agent_id="crow_handler-t001",
        ticket_id="t001",
        session="handler-log",
        crow_session=SESSION,
        harness=ClaudeCodeAdapter(),
        config=CrowHandlerConfig(model="test", poll_interval_s=999.0),
        repo_root=tmp_path,
        runtime=runtime,
        outcome=outcome,
        coordinator=coordinator,
    )


def test_queue_message_sends_immediately_when_idle(handler, fake_tmux):
    handler._idle_cached = True
    result = asyncio.run(handler.queue_message("nudge"))
    assert result == {"queued": False}
    send_calls = fake_tmux.calls_to("send_keys")
    assert len(send_calls) == 1
    (session_arg, text), kw = send_calls[0]
    assert session_arg == SESSION
    assert text == "nudge"
    assert kw["enter"] is True


def test_queue_message_reports_immediate_delivery_failure(handler, fake_tmux, monkeypatch):
    async def _fail_send(_session: str, _msg: str):
        return fail_result("send failed")

    monkeypatch.setattr(handler.harness, "send_prompt", _fail_send)
    handler._idle_cached = True

    result = asyncio.run(handler.queue_message("nudge"))

    assert result == {"queued": False, "ok": False, "error": "send failed"}
    assert fake_tmux.calls_to("send_keys") == []


def test_queue_message_holds_until_idle(handler, fake_tmux, monkeypatch):
    monkeypatch.setattr(
        "murder.state.persistence.tickets.get_ticket_status",
        lambda _db, _tid: "in_progress",
    )
    monkeypatch.setattr("murder.state.persistence.agents.heartbeat_agent", lambda *_a, **_k: None)
    handler._idle_cached = False
    result = asyncio.run(handler.queue_message("wait for idle"))
    assert result == {"queued": True}
    assert handler.pending_message == "wait for idle"
    assert fake_tmux.calls_to("send_keys") == []

    fake_tmux.reset_queue()
    fake_tmux.queue_pane(CC_IDLE)
    asyncio.run(handler.tick())
    assert handler.pending_message is None
    send_calls = fake_tmux.calls_to("send_keys")
    assert len(send_calls) == 1
    assert send_calls[0][0][1] == "wait for idle"


def test_interrupt_crow_sends_escape(handler, fake_tmux):
    asyncio.run(handler.interrupt_crow())
    send_calls = fake_tmux.calls_to("send_keys")
    assert len(send_calls) == 1
    assert send_calls[0][0] == (SESSION, "Escape")
