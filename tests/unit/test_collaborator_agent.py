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
CC_BUSY = (_FIXTURES / "cc_busy.txt").read_text(encoding="utf-8")


def test_start_rearms_idle_gate_so_first_user_send_waits_for_input(
    fake_tmux: FakeTmux,
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Regression: the brief send returns while the harness is still working on
    the brief. The first real user message (delivered by the worker right after
    ensure_collaborator() returns) must wait for the pane to come back to
    input-ready instead of landing keystrokes in a busy harness — where the text
    would sit unsubmitted and never run as a turn (the observed
    collaborator-never-runs-a-turn bug). Mirrors the Crow deliver-when-idle gate.
    """

    async def _session_exists(_session: str) -> bool:
        return True

    monkeypatch.setattr(tmux, "session_exists", _session_exists)
    # Boot: every poll during start() sees an idle pane.
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

    # The gate must be re-armed after the brief: the next send is a *first* send
    # again and is obligated to wait for input-ready.
    assert agent.harness_session._first_send_idle_gate_pending is True  # noqa: SLF001

    # Now the harness is busy with the brief, then returns to idle. The user send
    # must poll the busy pane (and NOT deliver) until the idle pane appears.
    fake_tmux.reset_queue()
    fake_tmux.queue_pane(CC_BUSY)  # first input-ready poll: still working
    fake_tmux.queue_pane(CC_IDLE)  # second poll: back to input-ready

    send_calls_before = len(fake_tmux.calls_to("send_keys"))
    result = asyncio.run(agent.send("real user question"))

    assert result.ok
    # The user text was delivered exactly once, only after the gate cleared.
    user_sends = [
        args for args, _kw in fake_tmux.calls_to("send_keys") if args[1] == "real user question"
    ]
    assert len(user_sends) == 1
    # And the gate forced at least one input-ready poll before delivery: more
    # capture_pane calls than a gateless straight-through send would make.
    assert len(fake_tmux.calls_to("send_keys")) == send_calls_before + 1
    assert any(
        name == "capture_pane" for name, *_ in fake_tmux.calls
    ), "gate must poll the pane for input-ready before delivering"
    # Gate is consumed by this delivery (one-shot).
    assert agent.harness_session._first_send_idle_gate_pending is False  # noqa: SLF001


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
    acc_first = agent._accumulator  # noqa: SLF001 - persistent accumulator seam
    asyncio.run(agent.refresh_transcript())

    assert ("user", "real question") in turns
    # The injected brief must never surface as a turn.
    assert all("fresh brief" not in body for _role, body in turns)
    # One persistent accumulator is reused across refreshes (incremental scrollback).
    assert acc_first is not None
    assert agent._accumulator is acc_first  # noqa: SLF001


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
