"""Regression tests for ticket vs rogue crow launch ordering (launch fix plan §7)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

import murder.runtime.terminal.tmux as tmux_mod
from murder.runtime.agents.base import AgentStatus
from murder.runtime.agents.crow import CrowAgent
from murder.llm.harnesses.antigravity import AntigravityAdapter
from murder.llm.harnesses.base import HarnessSession
from murder.llm.harnesses.claude_code import ClaudeCodeAdapter
from murder.llm.harnesses.codex import CodexAdapter
from murder.llm.harnesses.cursor import CursorAdapter
from murder.llm.harnesses.models import HarnessStartSpec
from murder.llm.harnesses.results import fail_result, ok_result
from tests.support.fake_tmux import FakeTmux

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "harness_panes"


def _load(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding="utf-8")


CC_IDLE = _load("cc_idle.txt")
CC_TRUST = _load("cc_trust_dialog.txt")
CURSOR_IDLE = _load("cursor_idle.txt")
# Idle pane with no parseable active-model status line, so cursor's set_model
# verification falls back to curated membership and confirms the request.
CURSOR_IDLE_GPT = CURSOR_IDLE.replace("Composer 2.5", "GPT-5.5")
CODEX_IDLE = _load("codex_idle.txt")


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


# ── 7.1 — ticket crow: model before brief ────────────────────────────────────


def test_crow_agent_start_applies_model_before_brief(fake_tmux_launch: FakeTmux) -> None:
    fake_tmux_launch.queue_pane(CURSOR_IDLE_GPT)

    agent = CrowAgent(
        agent_id="crow-t001",
        ticket_id="t001",
        session="crow-t001",
        harness=CursorAdapter(startup_model="gpt-5.5"),
        repo_root=Path("/tmp/test-repo"),
        startup_model="gpt-5.5",
    )
    brief = "implement the widget per ticket t001"

    asyncio.run(agent.start(brief, {}))

    texts = _send_texts(fake_tmux_launch)
    model_idx = next(i for i, t in enumerate(texts) if t.startswith("/model"))
    brief_idx = next(i for i, t in enumerate(texts) if brief in t)
    assert model_idx < brief_idx
    assert "create_session" in fake_tmux_launch.call_names()


# ── 7.2 — rogue: HarnessSession.start only, no brief paste ───────────────────


def test_rogue_harness_start_never_sends_brief(fake_tmux_launch: FakeTmux) -> None:
    fake_tmux_launch.queue_pane(CURSOR_IDLE_GPT)
    context_text = "rogue must not receive this context body"

    hs = HarnessSession(CursorAdapter(startup_model="gpt-5.5"), "rogue-sess", Path("/tmp/repo"))
    result = asyncio.run(
        hs.start(_fast_spec(startup_model="gpt-5.5", startup_effort=None))
    )

    assert result.ok
    texts = _send_texts(fake_tmux_launch)
    assert not any(context_text in t for t in texts)


# ── 7.3 — send_prompt failure after successful harness start ─────────────────


def test_crow_agent_start_fails_when_brief_paste_fails(
    fake_tmux_launch: FakeTmux, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_tmux_launch.queue_pane(CC_IDLE)

    agent = CrowAgent(
        agent_id="crow-t002",
        ticket_id="t002",
        session="crow-t002",
        harness=ClaudeCodeAdapter(),
        repo_root=Path("/tmp/test-repo"),
    )
    sync = MagicMock()
    agent.runtime = type("RT", (), {"sync_agent": sync})()

    async def _fail_paste(_prompt: str):
        return fail_result("paste boom")

    monkeypatch.setattr(agent.harness_session, "send_prompt", _fail_paste)

    with pytest.raises(RuntimeError, match="paste boom"):
        asyncio.run(agent.start("ticket context", {}))

    assert agent.status == AgentStatus.FAILED
    sync.assert_called()


# ── 7.4 — CC trust dismissed before /model ───────────────────────────────────


def test_cc_trust_dialog_dismissed_before_model_command(fake_tmux_launch: FakeTmux) -> None:
    fake_tmux_launch.queue_pane(CC_TRUST)
    fake_tmux_launch.queue_pane(CC_TRUST)
    fake_tmux_launch.queue_pane(CC_IDLE)

    hs = HarnessSession(ClaudeCodeAdapter(), "cc-sess", Path("/tmp/repo"))

    async def _spy_set_model(model: str, effort: str | None = None):
        await tmux_mod.send_keys(hs.session, f"/model {model}", literal=True, enter=True)
        return ok_result()

    hs.set_model = _spy_set_model  # type: ignore[method-assign]

    result = asyncio.run(
        hs._configure_started_session(_fast_spec(startup_model="haiku"))  # noqa: SLF001
    )

    assert result.ok
    texts = _send_texts(fake_tmux_launch)
    assert "1" in texts
    model_cmds = [i for i, t in enumerate(texts) if t.startswith("/model")]
    assert model_cmds, "expected /model during configured startup"
    assert texts.index("1") < model_cmds[0]


# ── 7.5 — effort defaults to medium when unset ───────────────────────────────


def test_configure_session_defaults_effort_to_medium(fake_tmux_launch: FakeTmux) -> None:
    fake_tmux_launch.queue_pane(CC_IDLE)
    captured: dict[str, object] = {}

    hs = HarnessSession(ClaudeCodeAdapter(), "claude-sess", Path("/tmp/repo"))

    async def _spy_set_model(model: str, effort: str | None = None):
        captured["effort"] = effort
        return ok_result()

    hs.set_model = _spy_set_model  # type: ignore[method-assign]

    result = asyncio.run(
        hs._configure_started_session(  # noqa: SLF001
            _fast_spec(startup_model="gpt-5.5", startup_effort=None)
        )
    )

    assert result.ok
    assert captured["effort"] == "medium"
    assert CodexAdapter.default_effort == "medium"


def test_antigravity_configure_session_keeps_effort_unset_when_omitted(
    fake_tmux_launch: FakeTmux,
) -> None:
    fake_tmux_launch.queue_pane(_load("agy_idle.txt"))
    captured: dict[str, object] = {}

    hs = HarnessSession(AntigravityAdapter(), "agy-sess", Path("/tmp/repo"))

    async def _spy_set_model(model: str, effort: str | None = None):
        captured["model"] = model
        captured["effort"] = effort
        return ok_result()

    hs.set_model = _spy_set_model  # type: ignore[method-assign]

    result = asyncio.run(
        hs._configure_started_session(  # noqa: SLF001
            _fast_spec(startup_model="gemini-3-1-pro", startup_effort=None)
        )
    )

    assert result.ok
    assert captured == {"model": "gemini-3-1-pro", "effort": None}


def test_configure_session_preserves_explicit_effort(fake_tmux_launch: FakeTmux) -> None:
    fake_tmux_launch.queue_pane(CC_IDLE)
    captured: dict[str, object] = {}

    hs = HarnessSession(ClaudeCodeAdapter(), "claude-sess", Path("/tmp/repo"))

    async def _spy_set_model(model: str, effort: str | None = None):
        captured["effort"] = effort
        return ok_result()

    hs.set_model = _spy_set_model  # type: ignore[method-assign]

    result = asyncio.run(
        hs._configure_started_session(  # noqa: SLF001
            _fast_spec(startup_model="gpt-5.5", startup_effort="high")
        )
    )

    assert result.ok
    assert captured["effort"] == "high"


def test_codex_startup_model_skips_runtime_picker(fake_tmux_launch: FakeTmux) -> None:
    fake_tmux_launch.queue_pane(CODEX_IDLE)
    hs = HarnessSession(CodexAdapter(), "codex-sess", Path("/tmp/repo"))

    async def _spy_set_model(model: str, effort: str | None = None):
        raise AssertionError(f"unexpected runtime selection for {model} {effort}")

    hs.set_model = _spy_set_model  # type: ignore[method-assign]

    result = asyncio.run(
        hs._configure_started_session(  # noqa: SLF001
            _fast_spec(startup_model="gpt-5.4-mini", startup_effort=None)
        )
    )

    assert result.ok


def test_codex_startup_nondefault_effort_drives_runtime_selection(
    fake_tmux_launch: FakeTmux,
) -> None:
    # The launch --model flag selects the model but carries no effort, so a
    # non-default startup effort must still run the runtime selection even
    # though the model itself is already in place.
    fake_tmux_launch.queue_pane(CODEX_IDLE)
    hs = HarnessSession(CodexAdapter(), "codex-sess", Path("/tmp/repo"))
    captured: dict[str, object] = {}

    async def _spy_set_model(model: str, effort: str | None = None):
        captured["model"] = model
        captured["effort"] = effort
        return ok_result()

    hs.set_model = _spy_set_model  # type: ignore[method-assign]

    result = asyncio.run(
        hs._configure_started_session(  # noqa: SLF001
            _fast_spec(startup_model="gpt-5.4-mini", startup_effort="high")
        )
    )

    assert result.ok
    assert captured == {"model": "gpt-5.4-mini", "effort": "high"}


# ── 7.6 — Cursor set_model rejection detection ───────────────────────────────


def test_cursor_set_model_returns_false_on_rejection(fake_tmux_launch: FakeTmux) -> None:
    fake_tmux_launch.queue_pane("unknown model 'xyz' is not supported")

    ok = asyncio.run(CursorAdapter().set_model("sess", "xyz"))

    assert ok is False


def test_cursor_set_model_returns_true_when_model_confirmed(fake_tmux_launch: FakeTmux) -> None:
    # No conflicting active-model status line on the pane, so verification
    # falls back to curated membership and confirms the requested model.
    fake_tmux_launch.queue_pane(CURSOR_IDLE_GPT)

    ok = asyncio.run(CursorAdapter().set_model("sess", "gpt-5.5"))

    assert ok is True
