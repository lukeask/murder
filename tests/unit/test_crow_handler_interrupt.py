"""CrowHandler.interrupt_crow.

Chat delivery to a crow flows through the crow *agent's* deliver-when-idle
queue (HarnessBackedAgent.queue_message), the single crow delivery path; the
handler no longer owns a chat queue (see test_agent_message_queue.py).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from murder.config import CrowHandlerConfig
from murder.llm.harnesses.claude_code import ClaudeCodeAdapter
from murder.runtime.agents.crow_handler import CrowHandler
from murder.runtime.orchestration.outcome import TicketOutcomeService
from tests.unit.test_harness_adapters import CC_BUSY

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


def test_interrupt_crow_routes_through_verified_agent(handler, fake_tmux):
    crow = MagicMock()
    crow.interrupt_verified_generation = AsyncMock(return_value=True)
    handler.runtime.get_crow.return_value = crow

    asyncio.run(handler.interrupt_crow())

    crow.interrupt_verified_generation.assert_awaited_once_with()
    assert fake_tmux.calls_to("send_keys") == []
