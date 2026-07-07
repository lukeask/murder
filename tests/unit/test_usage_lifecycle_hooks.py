"""Live-session usage sampling hooks on agent startup and shutdown."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from murder.config import Config, CrowHandlerConfig, HarnessRoleConfig, ProjectConfig
from murder.llm.harnesses.claude_code import ClaudeCodeAdapter
from murder.llm.harnesses.results import ok_result
from murder.llm.harnesses.usage_sampling import LiveSessionUsageResult
from murder.runtime.agents.collaborator import CollaboratorAgent
from murder.runtime.agents.crow import CrowAgent
from murder.runtime.agents.planning_agent import PlanningAgent
from tests.support.fake_tmux import FakeTmux

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "harness_panes"
CC_IDLE = (_FIXTURES / "cc_idle.txt").read_text(encoding="utf-8")


def _config() -> Config:
    return Config(
        project=ProjectConfig(name="repo"),
        collaborator=HarnessRoleConfig(harness="codex"),
        default_crow=HarnessRoleConfig(harness="codex"),
        crow_handler=CrowHandlerConfig(model="test-model"),
    )


def _runtime(conn, tmp_path: Path):
    return SimpleNamespace(
        db=conn,
        config=_config(),
        repo_root=tmp_path,
        bus=None,
        run_id=None,
        sync_agent=MagicMock(),
    )


@pytest.fixture
def fake_tmux(monkeypatch):
    import murder.runtime.terminal.tmux as tmux_mod

    ft = FakeTmux()
    ft.install(monkeypatch, tmux_mod)

    async def _noop_sleep(_: float = 0) -> None:
        pass

    monkeypatch.setattr("asyncio.sleep", _noop_sleep)
    monkeypatch.setattr(
        "murder.verdict.enforcement.git_diff.head_commit",
        AsyncMock(return_value="abc123"),
    )
    return ft


@pytest.fixture
def sample_mock(monkeypatch):
    mock = AsyncMock(return_value=LiveSessionUsageResult(outcome="stored"))
    monkeypatch.setattr(
        "murder.llm.harnesses.usage_sampling.sample_live_session_usage",
        mock,
    )
    return mock


def test_startup_samples_before_first_prompt(
    fake_tmux: FakeTmux,
    sample_mock: AsyncMock,
    tmp_path: Path,
) -> None:
    from murder.state.persistence.schema import get_db, init_db

    fake_tmux.set_session_exists(True)
    fake_tmux.queue_pane(CC_IDLE)
    conn = get_db(tmp_path / "state.db")
    init_db(conn)
    runtime = _runtime(conn, tmp_path)
    agent = CrowAgent(
        agent_id="crow-t1",
        ticket_id="t1",
        session="murder_test_crow",
        harness=ClaudeCodeAdapter(),
        repo_root=tmp_path,
        runtime=runtime,
    )

    order: list[str] = []
    original_send = agent.harness_session.send_prompt

    async def traced_send(*args, **kwargs):
        order.append("send_prompt")
        return await original_send(*args, **kwargs)

    async def traced_sample(agent_obj, ctx, trigger):
        order.append(f"sample:{trigger}")
        return LiveSessionUsageResult(outcome="stored")

    sample_mock.side_effect = traced_sample
    agent.harness_session.send_prompt = traced_send  # type: ignore[method-assign]

    asyncio.run(agent.start("system brief", {}))

    assert order[0] == "sample:agent_startup"
    assert "send_prompt" in order
    assert order.index("sample:agent_startup") < order.index("send_prompt")
    sample_mock.assert_awaited_once()
    assert sample_mock.await_args.args[2] == "agent_startup"


def test_collaborator_startup_samples_before_brief_send(
    fake_tmux: FakeTmux,
    sample_mock: AsyncMock,
    tmp_path: Path,
) -> None:
    from murder.state.persistence.schema import get_db, init_db

    fake_tmux.set_session_exists(True)
    fake_tmux.queue_pane(CC_IDLE)
    conn = get_db(tmp_path / "state.db")
    init_db(conn)
    agent = CollaboratorAgent(
        agent_id="collaborator-0",
        session="murder_test_collaborator",
        harness=ClaudeCodeAdapter(),
        repo_root=tmp_path,
        runtime=_runtime(conn, tmp_path),
    )

    order: list[str] = []
    original_send = agent.harness_session.send_prompt

    async def traced_send(*args, **kwargs):
        order.append("send_prompt")
        return await original_send(*args, **kwargs)

    sample_mock.side_effect = lambda *a, **kw: (
        order.append("sample:agent_startup") or LiveSessionUsageResult(outcome="stored")
    )

    agent.harness_session.send_prompt = traced_send  # type: ignore[method-assign]

    asyncio.run(agent.start("collab brief", {}))

    assert order.index("sample:agent_startup") < order.index("send_prompt")


def test_graceful_stop_samples_before_exit(
    fake_tmux: FakeTmux,
    sample_mock: AsyncMock,
    tmp_path: Path,
) -> None:
    from murder.state.persistence.schema import get_db, init_db

    conn = get_db(tmp_path / "state.db")
    init_db(conn)
    agent = CrowAgent(
        agent_id="crow-t1",
        ticket_id="t1",
        session="murder_test_crow",
        harness=ClaudeCodeAdapter(),
        repo_root=tmp_path,
        runtime=_runtime(conn, tmp_path),
    )
    fake_tmux.queue_pane(
        "Session ended.\nTo resume this session, run:\nclaude --resume abc123-deadbeef"
    )

    order: list[str] = []

    async def traced_sample(agent_obj, ctx, trigger):
        order.append(f"sample:{trigger}")
        return LiveSessionUsageResult(outcome="stored")

    sample_mock.side_effect = traced_sample

    asyncio.run(agent.stop(failed=False, kill_session=True))

    exit_calls = [
        args for name, args, _ in fake_tmux.calls if name == "send_keys" and args[1] == "/exit"
    ]
    assert order == ["sample:agent_shutdown"]
    assert exit_calls, "graceful stop must still send /exit after sampling"
    assert sample_mock.await_args.args[2] == "agent_shutdown"


def test_planning_graceful_stop_samples_before_interrupt(
    fake_tmux: FakeTmux,
    sample_mock: AsyncMock,
    tmp_path: Path,
) -> None:
    from murder.state.persistence.schema import get_db, init_db

    conn = get_db(tmp_path / "state.db")
    init_db(conn)
    agent = PlanningAgent(
        agent_id="planner-planA",
        session="murder_test_planner",
        plan_name="planA",
        harness=ClaudeCodeAdapter(),
        repo_root=tmp_path,
        runtime=_runtime(conn, tmp_path),
    )
    order: list[str] = []

    async def traced_sample(agent_obj, ctx, trigger):
        order.append(f"sample:{trigger}")
        return LiveSessionUsageResult(outcome="stored")

    sample_mock.side_effect = traced_sample

    async def traced_interrupt():
        order.append("interrupt")
        return ok_result(None)

    agent.harness_session.interrupt = traced_interrupt  # type: ignore[method-assign]

    asyncio.run(agent.stop(failed=False, kill_session=True))

    assert order == ["sample:agent_shutdown", "interrupt"]


@pytest.mark.parametrize(
    ("failed", "kill_session"),
    [
        (True, True),
        (False, False),
    ],
)
def test_hard_or_preserve_stop_skips_shutdown_sampling(
    fake_tmux: FakeTmux,
    sample_mock: AsyncMock,
    tmp_path: Path,
    failed: bool,
    kill_session: bool,
) -> None:
    from murder.state.persistence.schema import get_db, init_db

    conn = get_db(tmp_path / "state.db")
    init_db(conn)
    agent = CrowAgent(
        agent_id="crow-t1",
        ticket_id="t1",
        session="murder_test_crow",
        harness=ClaudeCodeAdapter(),
        repo_root=tmp_path,
        runtime=_runtime(conn, tmp_path),
    )

    asyncio.run(agent.stop(failed=failed, kill_session=kill_session))

    sample_mock.assert_not_awaited()


def test_sampler_skip_does_not_block_startup(
    fake_tmux: FakeTmux,
    sample_mock: AsyncMock,
    tmp_path: Path,
) -> None:
    from murder.runtime.agents.base import AgentStatus
    from murder.state.persistence.schema import get_db, init_db

    fake_tmux.set_session_exists(True)
    fake_tmux.queue_pane(CC_IDLE)
    conn = get_db(tmp_path / "state.db")
    init_db(conn)
    agent = CrowAgent(
        agent_id="crow-t1",
        ticket_id="t1",
        session="murder_test_crow",
        harness=ClaudeCodeAdapter(),
        repo_root=tmp_path,
        runtime=_runtime(conn, tmp_path),
    )
    sample_mock.return_value = LiveSessionUsageResult(outcome="skipped", reason="not_idle")

    asyncio.run(agent.start("system brief", {}))

    assert agent.status == AgentStatus.RUNNING


def test_sampler_failure_does_not_block_graceful_stop(
    fake_tmux: FakeTmux,
    sample_mock: AsyncMock,
    tmp_path: Path,
) -> None:
    from murder.runtime.agents.base import AgentStatus
    from murder.state.persistence.schema import get_db, init_db

    conn = get_db(tmp_path / "state.db")
    init_db(conn)
    agent = CrowAgent(
        agent_id="crow-t1",
        ticket_id="t1",
        session="murder_test_crow",
        harness=ClaudeCodeAdapter(),
        repo_root=tmp_path,
        runtime=_runtime(conn, tmp_path),
    )
    sample_mock.return_value = LiveSessionUsageResult(outcome="failed", reason="boom")
    fake_tmux.queue_pane("idle pane")

    asyncio.run(agent.stop(failed=False, kill_session=True))

    assert agent.status == AgentStatus.DONE
    assert any(name == "send_keys" for name, *_ in fake_tmux.calls)
