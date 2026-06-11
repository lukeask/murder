"""Per-agent transcript projection via the passive project_once() seam.

Regression coverage for the bug where a *rogue* crow (ticketless CrowAgent with
no CrowHandler) never persisted its assistant reply: projection used to be
bolted onto CrowHandler's poll loop, so agents without a handler were orphaned.
project_once() now lives on HarnessBackedAgent and is driven by a single
service-owned ticker, so rogues, collaborators, crows, and planners all project.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from murder.bus import ConversationBlockEvent
from murder.llm.harnesses.claude_code import ClaudeCodeAdapter
from murder.runtime.agents.base import AgentStatus
from murder.runtime.agents.crow import CrowAgent
from murder.runtime.terminal import tmux
from murder.state.persistence.conversation import read_conversation_blocks
from murder.state.persistence.schema import get_db, init_db
from tests.support.fake_tmux import FakeTmux

_FRAMES_DIR = Path(__file__).parent.parent / "fixtures" / "transcripts" / "cc" / "frames"


def _last_frame() -> str:
    frames = sorted(_FRAMES_DIR.glob("*.txt"))
    return frames[-1].read_text(encoding="utf-8", errors="replace")


def _rogue(conn, bus, tmp_path: Path) -> CrowAgent:
    runtime = SimpleNamespace(db=conn, bus=bus, run_id="run-1", sync_agent=MagicMock())
    agent = CrowAgent(
        agent_id="claude-rogue-testingpostworker",
        ticket_id=None,
        session="murder_test_rogue",
        harness=ClaudeCodeAdapter(),
        repo_root=tmp_path,
        runtime=runtime,
    )
    agent.status = AgentStatus.RUNNING
    return agent


def test_rogue_project_once_persists_assistant_reply(
    fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """A rogue's assistant reply lands in conversation_blocks + emits an event,
    with no CrowHandler involved — driven solely by project_once()."""
    conn = get_db(tmp_path / "state.db")
    init_db(conn)
    bus = SimpleNamespace(publish=AsyncMock())
    agent = _rogue(conn, bus, tmp_path)

    # spawn_rogue calls start_conversation() directly (it bypasses CrowAgent.start);
    # that builds the producer with no I/O and no background task.
    agent.start_conversation()
    fake_tmux.queue_pane(_last_frame())

    asyncio.run(agent.project_once())

    blocks = read_conversation_blocks(conn, "claude-rogue-testingpostworker")
    kinds = {b.kind for b in blocks}
    assert kinds & {"assistant_final", "assistant_intermediate"}, kinds
    bus.publish.assert_awaited()
    # The tick publishes block events for the parsed content, then a trailing
    # conversation.state push (live_state/queued pair) — assert the block event
    # among all published events rather than positionally.
    events = [call.args[0] for call in bus.publish.await_args_list]
    block_events = [e for e in events if isinstance(e, ConversationBlockEvent)]
    assert block_events, events
    assert block_events[-1].conversation_id == "claude-rogue-testingpostworker"


def test_project_once_is_noop_without_producer(fake_tmux: FakeTmux, tmp_path: Path) -> None:
    """No db ⇒ no producer ⇒ project_once does not even touch the pane."""
    bus = SimpleNamespace(publish=AsyncMock())
    runtime = SimpleNamespace(db=None, bus=bus, run_id="run-1", sync_agent=MagicMock())
    agent = CrowAgent(
        agent_id="claude-rogue-x",
        ticket_id=None,
        session="murder_test_rogue",
        harness=ClaudeCodeAdapter(),
        repo_root=tmp_path,
        runtime=runtime,
    )
    agent.status = AgentStatus.RUNNING
    agent.start_conversation()  # db is None ⇒ producer stays None

    asyncio.run(agent.project_once())

    assert fake_tmux.calls_to("capture_pane") == []
    bus.publish.assert_not_awaited()


def test_project_once_noop_when_terminal(fake_tmux: FakeTmux, tmp_path: Path) -> None:
    """A terminal agent (session gone) is skipped without capturing the pane."""
    conn = get_db(tmp_path / "state.db")
    init_db(conn)
    bus = SimpleNamespace(publish=AsyncMock())
    agent = _rogue(conn, bus, tmp_path)
    agent.start_conversation()
    agent.status = AgentStatus.DONE

    asyncio.run(agent.project_once())

    assert fake_tmux.calls_to("capture_pane") == []
