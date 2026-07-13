"""Tests for ticket and rogue crow launch sequencing invariants.

COOKBOOK = canonical launch ordering: verified control before brief,
           rogue-never-briefed.
EDGE CASES = brief-paste failure and no legacy model/prompt ownership.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import murder.runtime.terminal.tmux as tmux_mod
from murder.llm.harness_control.runtime.prompt_driver import PromptDriverPolicy
from murder.llm.harnesses.base import HarnessSession
from murder.llm.harnesses.claude_code import ClaudeCodeAdapter
from murder.llm.harnesses.cursor import CursorAdapter
from murder.llm.harnesses.models import HarnessStartSpec
from murder.runtime.agents.base import AgentStatus
from murder.runtime.agents.crow import CrowAgent
from murder.state.persistence.schema import get_db, init_db
from tests.support.fake_tmux import FakeTmux

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "harness_panes"


def _load(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding="utf-8")


CC_IDLE = _load("cc_idle.txt")
CC_TRUST = _load("cc_trust_dialog.txt")
CURSOR_IDLE = _load("cursor_idle.txt")
CURSOR_IDLE_GPT = CURSOR_IDLE.replace("Composer 2.5", "GPT-5.5")
CURSOR_FILTERED_GPT = """
Available models

 Filter: GPT-5.5

 → GPT-5.5                  272K Medium

 1-1 of 1

 Type to filter • Enter to select • Tab to edit
"""
CODEX_IDLE = _load("codex_idle.txt")
MINIMUM_EVIDENCE_RECORDS = 3


@pytest.fixture
def fake_tmux_launch(monkeypatch):
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


def _fast_spec(**kwargs) -> HarnessStartSpec:
    base = dict(cwd=Path("/tmp/test-repo"), ready_timeout_s=0.4, poll_interval_s=0.4)
    base.update(kwargs)
    return HarnessStartSpec(**base)


def _send_texts(ft: FakeTmux) -> list[str]:
    return [args[1] for args, _ in ft.calls_to("send_keys")]


def _verified_runtime(tmp_path: Path) -> SimpleNamespace:
    connection = get_db(tmp_path / "state.db")
    init_db(connection)

    async def no_sleep(_: float) -> None:
        return None

    return SimpleNamespace(
        db=connection,
        bus=None,
        run_id=None,
        sync_agent=MagicMock(),
        verified_prompt_driver_policy=PromptDriverPolicy(
            observation_interval=timedelta(), maximum_observations=12
        ),
        verified_prompt_driver_sleep=no_sleep,
    )


def _script_verified_claude_prompt(ft: FakeTmux, text: str) -> None:
    visible = CC_IDLE.replace('❯\xa0Try "create a util logging.py that..."', f"❯ {text}")
    ft.queue_pane_after_effect(visible, effect="paste_buffer_literal", effect_text=text)
    ft.queue_pane_after_effect(CC_IDLE, effect="send_keys", effect_text="Enter")


def _script_visible_payload_without_acknowledgment(ft: FakeTmux, text: str) -> None:
    visible = CC_IDLE.replace('❯\xa0Try "create a util logging.py that..."', f"❯ {text}")
    ft.queue_pane_after_effect(visible, effect="paste_buffer_literal", effect_text=text)


def _assert_verified_prompt_trace(connection, ft: FakeTmux) -> None:
    assert connection.execute("SELECT COUNT(*) FROM harness_control_operations").fetchone()[0] == 1
    assert (
        connection.execute("SELECT COUNT(*) FROM harness_control_evidence").fetchone()[0]
        >= MINIMUM_EVIDENCE_RECORDS
    )
    enters = [args for args, _ in ft.calls_to("send_keys") if args[1] == "Enter"]
    assert len(enters) == 1

# ============================================================
# === COOKBOOK ===============================================
# ============================================================

# ── 7.1 — ticket crow: model before brief ────────────────────────────────────


def test_crow_agent_start_submits_brief_through_verified_control(
    fake_tmux_launch: FakeTmux, tmp_path: Path
) -> None:
    """Crow startup needs fresh acknowledgment, not a legacy send result."""

    fake_tmux_launch.queue_pane(CC_IDLE)
    brief = "implement the widget per ticket t001"
    _script_verified_claude_prompt(fake_tmux_launch, brief)
    runtime = _verified_runtime(tmp_path)

    agent = CrowAgent(
        agent_id="crow-t001",
        ticket_id="t001",
        session="crow-t001",
        harness=ClaudeCodeAdapter(),
        repo_root=tmp_path,
        runtime=runtime,
    )

    asyncio.run(agent.start(brief, {}))

    assert agent.status is AgentStatus.RUNNING
    _assert_verified_prompt_trace(runtime.db, fake_tmux_launch)
    assert "create_session" in fake_tmux_launch.call_names()


# ── 7.2 — rogue: HarnessSession.start only, no brief paste ───────────────────


def test_rogue_harness_start_never_sends_brief(fake_tmux_launch: FakeTmux) -> None:
    fake_tmux_launch.queue_pane(CURSOR_IDLE_GPT)
    context_text = "rogue must not receive this context body"

    hs = HarnessSession(CursorAdapter(startup_model="gpt-5.5"), "rogue-sess", Path("/tmp/repo"))
    result = asyncio.run(hs.start(_fast_spec(startup_model="gpt-5.5", startup_effort=None)))

    assert result.ok
    texts = _send_texts(fake_tmux_launch)
    assert not any(context_text in t for t in texts)


# ============================================================
# === EDGE CASES =============================================
# ============================================================

# ── 7.3 — send_prompt failure after successful harness start ─────────────────


def test_crow_agent_start_escalates_when_enter_lacks_fresh_acknowledgment(
    fake_tmux_launch: FakeTmux, tmp_path: Path
) -> None:
    """The startup path must not pretend that a successful Enter submitted."""

    fake_tmux_launch.queue_pane(CC_IDLE)
    _script_visible_payload_without_acknowledgment(fake_tmux_launch, "ticket context")
    runtime = _verified_runtime(tmp_path)

    agent = CrowAgent(
        agent_id="crow-t002",
        ticket_id="t002",
        session="crow-t002",
        harness=ClaudeCodeAdapter(),
        repo_root=tmp_path,
        runtime=runtime,
    )

    with pytest.raises(RuntimeError, match="verified prompt submission escalated"):
        asyncio.run(agent.start("ticket context", {}))

    assert agent.status == AgentStatus.FAILED
    runtime.sync_agent.assert_called()
    enters = [args for args, _ in fake_tmux_launch.calls_to("send_keys") if args[1] == "Enter"]
    assert len(enters) == 1


def test_crow_agent_send_reports_verified_escalation_after_ambiguous_commit(
    fake_tmux_launch: FakeTmux, tmp_path: Path
) -> None:
    """No adapter ``send_prompt`` seam remains to fake delivery success."""

    fake_tmux_launch.queue_pane(CC_IDLE)
    _script_visible_payload_without_acknowledgment(fake_tmux_launch, "hello")
    runtime = _verified_runtime(tmp_path)
    agent = CrowAgent(
        agent_id="crow-t003",
        ticket_id="t003",
        session="crow-t003",
        harness=ClaudeCodeAdapter(),
        repo_root=tmp_path,
        runtime=runtime,
    )
    asyncio.run(agent.initialize_verified_harness_control())

    result = asyncio.run(agent.send("hello"))

    assert not result.ok
    assert result.message == "verified prompt submission escalated"
    enters = [args for args, _ in fake_tmux_launch.calls_to("send_keys") if args[1] == "Enter"]
    assert len(enters) == 1
