"""Tests for murder.runtime.agents.collaborator (CollaboratorAgent).

COOKBOOK = canonical start/stop lifecycle, conversation block publishing.
EDGE CASES = real failure modes: stale conversation cleared on restart,
             ground-truth block survives pane re-parse, startup failure
             records notice, stop variants (clean vs preserve-session).
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from murder.runtime.orchestration.events import ConversationBlockEvent
from murder.llm.harness_control.runtime.prompt_driver import PromptDriverPolicy
from murder.llm.harnesses.claude_code import ClaudeCodeAdapter
from murder.llm.harnesses.results import fail_result
from murder.runtime.agents.base import AgentStatus
from murder.runtime.agents.collaborator import CollaboratorAgent
from murder.state.persistence.conversation import read_conversation_blocks, upsert_conversation
from murder.state.persistence.schema import get_db, init_db
from tests.support.fake_tmux import FakeTmux

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "harness_panes"
CC_IDLE = (_FIXTURES / "cc_idle.txt").read_text(encoding="utf-8")
CC_BUSY = (_FIXTURES / "cc_busy.txt").read_text(encoding="utf-8")
PROMPT_COUNT = 2


async def _no_sleep(_: float) -> None:
    """Keep reconciliation traces deterministic without making them timing tests."""


def _runtime(conn: object, *, bus: object | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        db=conn,
        bus=bus,
        run_id="run-1" if bus is not None else None,
        sync_agent=MagicMock(),
        verified_prompt_driver_policy=PromptDriverPolicy(
            observation_interval=timedelta(), maximum_observations=12
        ),
        verified_prompt_driver_sleep=_no_sleep,
    )


def _composer_visible(text: str) -> str:
    return CC_IDLE.replace('❯\xa0Try "create a util logging.py that..."', f"❯ {text}")


def _script_acknowledged_submission(fake_tmux: FakeTmux, text: str) -> None:
    """Acknowledge only after the actuator has emitted the semantic commit."""

    fake_tmux.queue_pane_after_effect(
        _composer_visible(text), effect="paste_buffer_literal", effect_text=text
    )
    fake_tmux.queue_pane_after_effect(CC_IDLE, effect="send_keys", effect_text="Enter")


def _new_agent(
    *, fake_tmux: FakeTmux, tmp_path: Path, conn: object, bus: object | None = None
) -> tuple[CollaboratorAgent, SimpleNamespace]:
    fake_tmux.set_session_exists(True)
    fake_tmux.queue_pane(CC_IDLE)
    runtime = _runtime(conn, bus=bus)
    agent = CollaboratorAgent(
        agent_id="collaborator-0",
        session="murder_test_collaborator",
        harness=ClaudeCodeAdapter(),
        repo_root=tmp_path,
        runtime=runtime,
    )
    return agent, runtime


def test_start_and_followup_use_verified_prompt_control(
    fake_tmux: FakeTmux,
    tmp_path: Path,
) -> None:
    """Messages use persisted verified control rather than adapter send_prompt."""

    conn = get_db(tmp_path / "state.db")
    init_db(conn)
    agent, runtime = _new_agent(fake_tmux=fake_tmux, tmp_path=tmp_path, conn=conn)
    _script_acknowledged_submission(fake_tmux, "fresh brief")

    asyncio.run(agent.start("fresh brief", {}))

    _script_acknowledged_submission(fake_tmux, "real user question")
    result = asyncio.run(agent.send("real user question"))

    assert result.ok
    enters = [args for args, _kw in fake_tmux.calls_to("send_keys") if args[1] == "Enter"]
    assert len(enters) == PROMPT_COUNT
    assert not hasattr(agent.harness_session, "send_prompt")

    # The raw terminal fact, broad parser evidence, semantic operation, and
    # emitted action are all durable.  A tmux Enter alone is never the result.
    assert conn.execute("SELECT COUNT(*) FROM harness_control_frames").fetchone()[0] > 0
    assert conn.execute("SELECT COUNT(*) FROM harness_control_evidence").fetchone()[0] > 0
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM harness_control_operations WHERE capability = 'submit_prompt'"
        ).fetchone()[0]
        == PROMPT_COUNT
    )
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM harness_control_actions "
            "WHERE semantic_action_type LIKE '%CommitPromptSubmission' "
            "AND emission_status = 'EMITTED'"
        ).fetchone()[0]
        == PROMPT_COUNT
    )
    calls = fake_tmux.calls
    final_enter = max(
        index
        for index, (name, args, _kwargs) in enumerate(calls)
        if name == "send_keys" and args[1] == "Enter"
    )
    assert any(name == "capture_pane" for name, _args, _kwargs in calls[final_enter + 1 :])
    runtime.sync_agent.assert_called_with(agent)


def test_collaborator_send_escalates_when_enter_has_no_later_acknowledgment(
    fake_tmux: FakeTmux,
    tmp_path: Path,
) -> None:
    """Commit emission remains ambiguous when only pre-Enter evidence is visible."""

    conn = get_db(tmp_path / "state.db")
    init_db(conn)
    agent, _runtime_scope = _new_agent(
        fake_tmux=fake_tmux, tmp_path=tmp_path, conn=conn
    )
    _script_acknowledged_submission(fake_tmux, "fresh brief")
    asyncio.run(agent.start("fresh brief", {}))

    # The fake updates for insertion but deliberately not for Enter.  The
    # controller must observe, then escalate; it must not replay the unsafe
    # commit action to make progress.
    fake_tmux.queue_pane_after_effect(
        _composer_visible("ambiguous question"),
        effect="paste_buffer_literal",
        effect_text="ambiguous question",
    )
    result = asyncio.run(agent.send("ambiguous question"))

    assert not result.ok
    assert "escalated" in (result.message or "")
    enter_calls = [args for args, _ in fake_tmux.calls_to("send_keys") if args[1] == "Enter"]
    assert len(enter_calls) == PROMPT_COUNT  # startup acknowledgment + one ambiguous commit
    assert not hasattr(agent.harness_session, "send_prompt")
    latest = conn.execute(
        "SELECT status FROM harness_control_operations "
        "WHERE capability = 'submit_prompt' ORDER BY updated_at DESC LIMIT 1"
    ).fetchone()
    assert latest["status"] == "ESCALATED"
    assert conn.execute("SELECT COUNT(*) FROM harness_control_evidence").fetchone()[0] > 0


# ============================================================
# === COOKBOOK ===============================================
# ============================================================


def test_collaborator_start_clears_prior_conversation(
    fake_tmux: FakeTmux,
    tmp_path: Path,
) -> None:
    conn = get_db(tmp_path / "state.db")
    init_db(conn)
    conn.execute(
        "INSERT INTO agent_messages(agent_id, ordinal, role, body, captured_at) "
        "VALUES ('collaborator-0', 0, 'user', 'stale', '2026-06-02T00:00:00Z')"
    )
    runtime = _runtime(conn)
    agent = CollaboratorAgent(
        agent_id="collaborator-0",
        session="murder_test_collaborator",
        harness=ClaudeCodeAdapter(),
        repo_root=tmp_path,
        runtime=runtime,
    )

    # Conversation reset is an ownership concern, not a reason to exercise a
    # procedural prompt sender.  Prompt behavior is covered by verified traces
    # above.
    agent.start_conversation()

    rows = conn.execute(
        "SELECT body FROM agent_messages WHERE agent_id = 'collaborator-0'"
    ).fetchall()
    assert rows == []


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


def test_stop_clean_sets_conversation_complete_without_legacy_exit_scrape(
    fake_tmux: FakeTmux,
    tmp_path: Path,
) -> None:
    """Clean stop completes the conversation without unowned `/exit` input."""
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
    asyncio.run(agent.stop(failed=False, kill_session=True))

    row = conn.execute(
        "SELECT status, harness_session_id FROM conversations"
        " WHERE conversation_id = 'collaborator-0'"
    ).fetchone()
    assert row["status"] == "complete"
    assert row["harness_session_id"] is None
    assert not any(name == "send_keys" for name, _args, _kwargs in fake_tmux.calls)


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
    tmp_path: Path,
) -> None:
    """Phase 1.c server-side path: a user turn recorded authoritatively at the
    send boundary survives a subsequent pane parse (which never re-derives it),
    and the projector reuses one persistent producer across refreshes.
    """

    conn = get_db(tmp_path / "state.db")
    init_db(conn)
    runtime = _runtime(conn)
    agent = CollaboratorAgent(
        agent_id="collaborator-0",
        session="murder_test_collaborator",
        harness=ClaudeCodeAdapter(),
        repo_root=tmp_path,
        runtime=runtime,
    )
    agent.start_conversation()
    fake_tmux.queue_pane(CC_IDLE)

    # Ground truth recorded at send boundary, then the pane is parsed.
    agent.record_user_block("real question")
    turns = asyncio.run(agent.refresh_transcript())
    # agent._producer is the single per-conversation parser: pin the invariant
    # that the same producer object is reused across refreshes (incremental
    # scrollback now lives in the producer's accumulator, not a second one).
    producer_first = agent._producer  # noqa: SLF001
    asyncio.run(agent.refresh_transcript())

    assert ("user", "real question") in turns
    # The injected brief must never surface as a turn.
    assert all("fresh brief" not in body for _role, body in turns)
    # One persistent producer is reused across refreshes (incremental scrollback).
    assert producer_first is not None
    assert agent._producer is producer_first  # noqa: SLF001


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
