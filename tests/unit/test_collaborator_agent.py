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

import pytest

from murder.llm.harness_control.runtime.prompt_driver import PromptDriverPolicy
from murder.llm.harnesses.claude_code import ClaudeCodeAdapter
from murder.llm.harnesses.results import fail_result
from murder.runtime.agents.base import AgentStatus
from murder.runtime.agents.collaborator import CollaboratorAgent
from murder.runtime.orchestration.events import ConversationBlockEvent
from murder.state.persistence.connection import RepoDb
from murder.state.persistence.conversation import read_conversation_blocks, upsert_conversation
from murder.user_config import TuiUserConfig, UserConfig
from tests.support.database import open_test_repo_db
from tests.support.fake_tmux import FakeTmux

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "harness_panes"
CC_IDLE = (_FIXTURES / "cc_idle.txt").read_text(encoding="utf-8")
CC_BUSY = (_FIXTURES / "cc_busy.txt").read_text(encoding="utf-8")
PROMPT_COUNT = 2
BACKEND_CONNECTION_COUNT = 2


@pytest.fixture(autouse=True)
def _skip_live_usage_sampling(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests assert prompt Enter traces; keep startup/shutdown usage off-path."""
    from murder.llm.harnesses.usage_sampling import LiveSessionUsageResult

    monkeypatch.setattr(
        "murder.llm.harnesses.usage_sampling.sample_live_session_usage",
        AsyncMock(return_value=LiveSessionUsageResult(outcome="skipped", reason="test")),
    )


async def _no_sleep(_: float) -> None:
    """Keep reconciliation traces deterministic without making them timing tests."""


async def _stub_transition(
    agent: object,
    *,
    from_status: object = None,
    to_status: AgentStatus,
    reason: object = None,
) -> None:
    del from_status, reason
    agent.status = to_status  # type: ignore[attr-defined]


def _db(tmp_path: Path) -> RepoDb:
    return open_test_repo_db(tmp_path / "state.db")


def _runtime(
    conn: RepoDb, *, events: object | None = None, repo_root: Path | None = None
) -> SimpleNamespace:
    from murder.runtime.agents.verified_control import VerifiedControlFactory
    from murder.runtime.sessions.service import SessionService
    from tests.support.orchestrator import default_test_config

    sessions = SessionService(conn)
    factory = VerifiedControlFactory(
        db=conn,
        sessions=sessions,
        prompt_policy=PromptDriverPolicy(
            observation_interval=timedelta(), maximum_observations=12
        ),
        prompt_sleep=_no_sleep,
    )
    return SimpleNamespace(
        db=conn,
        sessions=sessions,
        config=default_test_config(),
        repo_root=repo_root or Path("."),
        orchestration_events=events,
        run_id="run-1" if events is not None else None,
        record=MagicMock(),
        transition=AsyncMock(side_effect=_stub_transition),
        initialize_verified_control=factory.initialize,
        structured_decisions=SimpleNamespace(observe=AsyncMock()),
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
    *, fake_tmux: FakeTmux, tmp_path: Path, conn: object, events: object | None = None
) -> tuple[CollaboratorAgent, SimpleNamespace]:
    fake_tmux.set_session_exists(True)
    fake_tmux.queue_pane(CC_IDLE)
    runtime = _runtime(conn, events=events, repo_root=tmp_path)
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

    conn = _db(tmp_path)
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
    assert (
        conn.conn.execute(
            "SELECT COUNT(*) FROM harness_control_frames WHERE repository_id = ?",
            (conn.repository_id,),
        ).fetchone()[0]
        > 0
    )
    assert (
        conn.conn.execute(
            "SELECT COUNT(*) FROM harness_control_evidence WHERE repository_id = ?",
            (conn.repository_id,),
        ).fetchone()[0]
        > 0
    )
    assert (
        conn.conn.execute(
            "SELECT COUNT(*) FROM harness_control_operations "
            "WHERE repository_id = ? AND capability = 'submit_prompt'",
            (conn.repository_id,),
        ).fetchone()[0]
        == PROMPT_COUNT
    )
    assert (
        conn.conn.execute(
            "SELECT COUNT(*) FROM harness_control_actions "
            "WHERE repository_id = ? "
            "AND semantic_action_type LIKE '%CommitPromptSubmission' "
            "AND emission_status = 'EMITTED'",
            (conn.repository_id,),
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
    runtime.transition.assert_awaited()
    assert runtime.transition.await_args.args[0] is agent
    assert runtime.transition.await_args.kwargs["to_status"] is AgentStatus.RUNNING


def test_collaborator_send_escalates_when_enter_has_no_later_acknowledgment(
    fake_tmux: FakeTmux,
    tmp_path: Path,
) -> None:
    """Commit emission remains ambiguous when only pre-Enter evidence is visible."""

    conn = _db(tmp_path)
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
    latest = conn.conn.execute(
        "SELECT status FROM harness_control_operations "
        "WHERE repository_id = ? AND capability = 'submit_prompt' "
        "ORDER BY updated_at DESC LIMIT 1",
        (conn.repository_id,),
    ).fetchone()
    assert latest["status"] == "ESCALATED"
    assert (
        conn.conn.execute(
            "SELECT COUNT(*) FROM harness_control_evidence WHERE repository_id = ?",
            (conn.repository_id,),
        ).fetchone()[0]
        > 0
    )


# ============================================================
# === COOKBOOK ===============================================
# ============================================================


def test_collaborator_start_clears_prior_conversation(
    fake_tmux: FakeTmux,
    tmp_path: Path,
) -> None:
    conn = _db(tmp_path)
    conn.conn.execute(
        "INSERT INTO agent_messages(repository_id, agent_id, ordinal, role, body, captured_at) "
        "VALUES (?, 'collaborator-0', 0, 'user', 'stale', '2026-06-02T00:00:00Z')",
        (conn.repository_id,),
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

    rows = conn.conn.execute(
        "SELECT body FROM agent_messages "
        "WHERE repository_id = ? AND agent_id = 'collaborator-0'",
        (conn.repository_id,),
    ).fetchall()
    assert rows == []


def test_record_user_block_event_publishes_conversation_block(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    bus = SimpleNamespace(publish=AsyncMock())
    runtime = SimpleNamespace(
        db=conn, orchestration_events=bus, run_id="run-1", record=MagicMock()
    )
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
    conn = _db(tmp_path)
    from tests.support.orchestrator import default_test_config

    runtime = SimpleNamespace(
        db=conn,
        orchestration_events=None,
        run_id=None,
        record=MagicMock(),
        config=default_test_config(),
        repo_root=tmp_path,
    )
    agent = CollaboratorAgent(
        agent_id="collaborator-0",
        session="murder_test_collaborator",
        harness=ClaudeCodeAdapter(),
        repo_root=tmp_path,
        runtime=runtime,
    )
    upsert_conversation(conn, conversation_id="collaborator-0", agent_id="collaborator-0")
    asyncio.run(agent.stop(failed=False, kill_session=True))

    row = conn.conn.execute(
        "SELECT status, harness_session_id FROM conversations"
        " WHERE repository_id = ? AND conversation_id = 'collaborator-0'",
        (conn.repository_id,),
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
    conn = _db(tmp_path)
    from tests.support.orchestrator import default_test_config

    runtime = SimpleNamespace(
        db=conn,
        orchestration_events=None,
        run_id=None,
        record=MagicMock(),
        config=default_test_config(),
        repo_root=tmp_path,
    )
    agent = CollaboratorAgent(
        agent_id="collaborator-0",
        session="murder_test_collaborator",
        harness=ClaudeCodeAdapter(),
        repo_root=tmp_path,
        runtime=runtime,
    )
    upsert_conversation(conn, conversation_id="collaborator-0", agent_id="collaborator-0")

    asyncio.run(agent.stop(failed=True, kill_session=False))

    row = conn.conn.execute(
        "SELECT status FROM conversations "
        "WHERE repository_id = ? AND conversation_id = 'collaborator-0'",
        (conn.repository_id,),
    ).fetchone()
    assert row["status"] == "in_progress"


def test_destructive_stop_closes_owned_backend_connections(
    fake_tmux: FakeTmux,
    tmp_path: Path,
) -> None:
    conn = _db(tmp_path)
    from tests.support.orchestrator import default_test_config

    runtime = SimpleNamespace(
        db=conn,
        orchestration_events=None,
        run_id=None,
        record=MagicMock(),
        config=default_test_config(),
        repo_root=tmp_path,
    )
    agent = CollaboratorAgent(
        agent_id="collaborator-0",
        session="murder_test_collaborator",
        harness=ClaudeCodeAdapter(),
        repo_root=tmp_path,
        runtime=runtime,
    )
    connections = [
        SimpleNamespace(aclose=AsyncMock()),
        SimpleNamespace(aclose=AsyncMock()),
        SimpleNamespace(aclose=AsyncMock()),
    ]
    (
        agent.app_server_connection,
        agent.acp_connection,
        agent.agent_sdk_connection,
    ) = connections

    asyncio.run(agent.stop(failed=False, kill_session=True))

    for connection in connections:
        connection.aclose.assert_awaited_once()
    assert agent.app_server_connection is None
    assert agent.acp_connection is None
    assert agent.agent_sdk_connection is None


def test_preserved_session_keeps_owned_backend_connections_open(
    fake_tmux: FakeTmux,
    tmp_path: Path,
) -> None:
    conn = _db(tmp_path)
    runtime = SimpleNamespace(
        db=conn, orchestration_events=None, run_id=None, record=MagicMock()
    )
    agent = CollaboratorAgent(
        agent_id="collaborator-0",
        session="murder_test_collaborator",
        harness=ClaudeCodeAdapter(),
        repo_root=tmp_path,
        runtime=runtime,
    )
    connection = SimpleNamespace(aclose=AsyncMock())
    agent.acp_connection = connection

    asyncio.run(agent.stop(failed=False, kill_session=False))

    connection.aclose.assert_not_awaited()
    assert agent.acp_connection is connection


def test_reinitialize_after_destructive_stop_creates_fresh_backend_connection(
    fake_tmux: FakeTmux,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """stop() clears owned backends so the next init bootstraps a new connection."""

    conn = _db(tmp_path)
    from murder.runtime.agents.verified_control import VerifiedControlFactory
    from tests.support.orchestrator import default_test_config

    factory = VerifiedControlFactory(db=conn)
    runtime = SimpleNamespace(
        db=conn,
        orchestration_events=None,
        run_id=None,
        record=MagicMock(),
        repo_root=tmp_path,
        config=default_test_config(),
        session_controllers=None,
        initialize_verified_control=factory.initialize,
    )
    agent = CollaboratorAgent(
        agent_id="collaborator-0",
        session="murder_test_collaborator",
        harness=ClaudeCodeAdapter(),
        repo_root=tmp_path,
        runtime=runtime,
    )
    monkeypatch.setattr(
        "murder.user_config.load_user_config",
        lambda: UserConfig(tui=TuiUserConfig(claude_control_backend="agent_sdk")),
    )
    bootstrapped: list[SimpleNamespace] = []

    async def _fake_start_agent_sdk_session(**_kwargs: object) -> tuple[SimpleNamespace, object]:
        connection = SimpleNamespace(aclose=AsyncMock())
        bootstrapped.append(connection)
        return connection, object()

    controller = SimpleNamespace(execute=AsyncMock())
    verified = SimpleNamespace(
        ensure_session_controller=AsyncMock(return_value=controller),
        remove_session_controller=AsyncMock(),
        session_controller=controller,
    )
    monkeypatch.setattr(
        "murder.llm.harness_control.agent_sdk.bootstrap.start_agent_sdk_session",
        _fake_start_agent_sdk_session,
    )
    monkeypatch.setattr(
        "murder.llm.harness_control.runtime.session.VerifiedHarnessControlSession.from_agent_sdk",
        lambda **_kwargs: verified,
    )

    asyncio.run(agent.initialize_verified_harness_control())
    first = agent.agent_sdk_connection
    assert first is bootstrapped[0]

    asyncio.run(agent.stop(failed=False, kill_session=True))
    first.aclose.assert_awaited_once()
    assert agent.agent_sdk_connection is None

    asyncio.run(agent.initialize_verified_harness_control())
    second = agent.agent_sdk_connection
    assert second is bootstrapped[1]
    assert second is not first
    assert len(bootstrapped) == BACKEND_CONNECTION_COUNT


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

    conn = _db(tmp_path)
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
    conn = _db(tmp_path)
    bus = SimpleNamespace(publish=AsyncMock())
    runtime = SimpleNamespace(
        db=conn, orchestration_events=bus, run_id="run-1", record=MagicMock()
    )
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
    runtime.record.assert_called_with(agent)
