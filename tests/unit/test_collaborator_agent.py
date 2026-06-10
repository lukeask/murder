"""Tests for murder.runtime.agents.collaborator (CollaboratorAgent).

COOKBOOK = canonical start/stop lifecycle, conversation block publishing.
EDGE CASES = real failure modes: stale conversation cleared on restart,
             ground-truth block survives pane re-parse, startup failure
             records notice, stop variants (clean vs preserve-session).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from murder.bus import ConversationBlockEvent
from murder.llm.harnesses.claude_code import ClaudeCodeAdapter
from murder.llm.harnesses.results import fail_result
from murder.runtime.agents.base import AgentStatus
from murder.runtime.agents.collaborator import CollaboratorAgent
from murder.runtime.terminal import tmux
from murder.state.persistence.conversation import read_conversation_blocks, upsert_conversation
from murder.state.persistence.schema import get_db, init_db
from tests.support.fake_tmux import FakeTmux

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "harness_panes"
CC_IDLE = (_FIXTURES / "cc_idle.txt").read_text(encoding="utf-8")


# ============================================================
# === COOKBOOK ===============================================
# ============================================================


def test_collaborator_start_clears_prior_conversation(
    fake_tmux: FakeTmux,
    monkeypatch,
    tmp_path: Path,
) -> None:
    async def _session_exists(_session: str) -> bool:
        return True

    monkeypatch.setattr(tmux, "session_exists", _session_exists)
    fake_tmux.queue_pane(CC_IDLE)
    conn = get_db(tmp_path / "state.db")
    init_db(conn)
    conn.execute(
        "INSERT INTO agent_messages(agent_id, ordinal, role, body, captured_at) "
        "VALUES ('collaborator-0', 0, 'user', 'stale', '2026-06-02T00:00:00Z')"
    )
    runtime = SimpleNamespace(db=conn, bus=None, run_id=None, sync_agent=MagicMock())
    agent = CollaboratorAgent(
        agent_id="collaborator-0",
        session="murder_test_collaborator",
        harness=ClaudeCodeAdapter(),
        repo_root=tmp_path,
        runtime=runtime,
    )

    asyncio.run(agent.start("fresh brief", {}))

    rows = conn.execute(
        "SELECT body FROM agent_messages WHERE agent_id = 'collaborator-0'"
    ).fetchall()
    assert rows == []
    runtime.sync_agent.assert_called_once_with(agent)


def test_record_user_block_event_publishes_conversation_block(tmp_path: Path) -> None:
    conn = get_db(tmp_path / "state.db")
    init_db(conn)
    bus = SimpleNamespace(publish=AsyncMock())
    runtime = SimpleNamespace(db=conn, bus=bus, run_id="run-1", sync_agent=MagicMock())
    agent = CollaboratorAgent(
        agent_id="collaborator-0",
        session="murder_test_collaborator",
        harness=ClaudeCodeAdapter(),
        repo_root=tmp_path,
        runtime=runtime,
    )

    asyncio.run(agent.record_user_block_event("real question"))

    bus.publish.assert_awaited_once()
    event = bus.publish.await_args.args[0]
    assert isinstance(event, ConversationBlockEvent)
    assert event.type == "conversation.block"
    assert event.conversation_id == "collaborator-0"
    assert event.action == "block-appended"
    assert event.block["kind"] == "user"
    assert event.block["payload"] == {"type": "user", "text": "real question"}


def test_stop_clean_sets_conversation_complete_and_captures_session_id(
    fake_tmux: FakeTmux,
    tmp_path: Path,
) -> None:
    """1.g: clean stop (kill_session=True, failed=False) transitions conversation
    to 'complete' and stores the harness resume session id."""
    conn = get_db(tmp_path / "state.db")
    init_db(conn)
    runtime = SimpleNamespace(db=conn, bus=None, run_id=None, sync_agent=MagicMock())
    agent = CollaboratorAgent(
        agent_id="collaborator-0",
        session="murder_test_collaborator",
        harness=ClaudeCodeAdapter(),
        repo_root=tmp_path,
        runtime=runtime,
    )
    upsert_conversation(conn, conversation_id="collaborator-0", agent_id="collaborator-0")
    fake_tmux.queue_pane(
        "Session ended.\nTo resume this session, run:\nclaude --resume abc123-deadbeef"
    )

    asyncio.run(agent.stop(failed=False, kill_session=True))

    row = conn.execute(
        "SELECT status, harness_session_id FROM conversations"
        " WHERE conversation_id = 'collaborator-0'"
    ).fetchone()
    assert row["status"] == "complete"
    assert row["harness_session_id"] == "abc123-deadbeef"


def test_stop_preserve_session_leaves_conversation_in_progress(
    fake_tmux: FakeTmux,
    tmp_path: Path,
) -> None:
    """1.g: graceful TUI-quit (kill_session=False) leaves conversation in_progress
    so next startup can mark it stale."""
    conn = get_db(tmp_path / "state.db")
    init_db(conn)
    runtime = SimpleNamespace(db=conn, bus=None, run_id=None, sync_agent=MagicMock())
    agent = CollaboratorAgent(
        agent_id="collaborator-0",
        session="murder_test_collaborator",
        harness=ClaudeCodeAdapter(),
        repo_root=tmp_path,
        runtime=runtime,
    )
    upsert_conversation(conn, conversation_id="collaborator-0", agent_id="collaborator-0")

    asyncio.run(agent.stop(failed=True, kill_session=False))

    row = conn.execute(
        "SELECT status FROM conversations WHERE conversation_id = 'collaborator-0'"
    ).fetchone()
    assert row["status"] == "in_progress"


# ============================================================
# === EDGE CASES =============================================
# ============================================================


def test_collaborator_ground_truth_block_survives_refresh(
    fake_tmux: FakeTmux,
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Phase 1.c server-side path: a user turn recorded authoritatively at the
    send boundary survives a subsequent pane parse (which never re-derives it),
    and the projector reuses one persistent accumulator across refreshes.
    """

    async def _session_exists(_session: str) -> bool:
        return True

    monkeypatch.setattr(tmux, "session_exists", _session_exists)
    fake_tmux.queue_pane(CC_IDLE)
    conn = get_db(tmp_path / "state.db")
    init_db(conn)
    runtime = SimpleNamespace(db=conn, bus=None, run_id=None, sync_agent=MagicMock())
    agent = CollaboratorAgent(
        agent_id="collaborator-0",
        session="murder_test_collaborator",
        harness=ClaudeCodeAdapter(),
        repo_root=tmp_path,
        runtime=runtime,
    )
    asyncio.run(agent.start("fresh brief", {}))

    # Ground truth recorded at send boundary, then the pane is parsed.
    agent.record_user_block("real question")
    turns = asyncio.run(agent.refresh_transcript())
    # agent._accumulator is a documented seam: pins the invariant that the
    # same accumulator object is reused across refreshes (incremental scrollback).
    acc_first = agent._accumulator  # noqa: SLF001
    asyncio.run(agent.refresh_transcript())

    assert ("user", "real question") in turns
    # The injected brief must never surface as a turn.
    assert all("fresh brief" not in body for _role, body in turns)
    # One persistent accumulator is reused across refreshes (incremental scrollback).
    assert acc_first is not None
    assert agent._accumulator is acc_first  # noqa: SLF001


def test_collaborator_start_failure_records_notice(tmp_path: Path) -> None:
    conn = get_db(tmp_path / "state.db")
    init_db(conn)
    bus = SimpleNamespace(publish=AsyncMock())
    runtime = SimpleNamespace(db=conn, bus=bus, run_id="run-1", sync_agent=MagicMock())
    agent = CollaboratorAgent(
        agent_id="collaborator-0",
        session="murder_test_collaborator",
        harness=ClaudeCodeAdapter(),
        repo_root=tmp_path,
        runtime=runtime,
    )
    agent.harness_session.start = AsyncMock(  # type: ignore[method-assign]
        return_value=fail_result("usage limit reached")
    )

    try:
        asyncio.run(agent.start("fresh brief", {}))
    except TimeoutError:
        pass
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("startup failure should propagate")

    assert agent.status == AgentStatus.FAILED
    blocks = read_conversation_blocks(conn, "collaborator-0")
    assert len(blocks) == 1
    assert blocks[0].kind == "notice"
    assert blocks[0].payload == {
        "type": "notice",
        "severity": "error",
        "message": "Collaborator startup failed: usage limit reached",
    }
    event = bus.publish.await_args.args[0]
    assert isinstance(event, ConversationBlockEvent)
    assert event.block["kind"] == "notice"
    runtime.sync_agent.assert_called_with(agent)
