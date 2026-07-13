"""Tests for ``HarnessSession`` — the startup and read-only facade.

Prompt delivery belongs exclusively to verified harness control.  These tests
cover startup, read-only parser/model behavior, and non-prompt terminal setup.

All tmux I/O is intercepted via ``FakeTmux`` (see ``tests/support/fake_tmux.py``).
``asyncio.sleep`` is patched to a no-op so timing delays don't slow tests.
Tests use ``asyncio.run()`` directly (consistent with the rest of the test suite).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import murder.runtime.terminal.tmux as tmux_mod
from murder.llm.harnesses.antigravity import AntigravityAdapter
from murder.llm.harnesses.base import HarnessAdapter, HarnessSession
from murder.llm.harnesses.claude_code import ClaudeCodeAdapter
from murder.llm.harnesses.codex import CodexAdapter
from murder.llm.harnesses.cursor import CursorAdapter
from murder.llm.harnesses.models import HarnessStartSpec
from tests.support.fake_tmux import FakeTmux

# ── Pane texts loaded from fixtures ──────────────────────────────────────────

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "harness_panes"


def _load(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding="utf-8")


CC_IDLE = _load("cc_idle.txt")
CC_BUSY = _load("cc_busy.txt")
CODEX_IDLE = _load("codex_idle.txt")
CODEX_STARTUP = _load("codex_startup.txt")
CODEX_IDLE_MINI = """
OpenAI Codex

›
gpt-5.4-mini · ~/repo
"""


# ── Shared fixture / helpers ──────────────────────────────────────────────────


@pytest.fixture
def fake_tmux(monkeypatch):
    ft = FakeTmux()
    ft.install(monkeypatch, tmux_mod)

    async def _noop_sleep(_: float = 0) -> None:
        pass

    monkeypatch.setattr("asyncio.sleep", _noop_sleep)
    return ft


def _start_spec(
    cwd: Path = Path("/tmp/test-repo"), *, model: str | None = None
) -> HarnessStartSpec:
    """Minimal spec that tries only 1 startup poll (avoids 600-iteration loops)."""
    return HarnessStartSpec(
        cwd=cwd,
        startup_model=model,
        ready_timeout_s=0.4,
        poll_interval_s=0.4,
    )


def _make_session(adapter=None, session: str = "test-session") -> HarnessSession:
    adapter = adapter or ClaudeCodeAdapter()
    return HarnessSession(adapter, session, Path("/tmp/test-repo"))


# ============================================================
# === COOKBOOK ===============================================
# ============================================================


def test_start_success_creates_tmux_session(fake_tmux: FakeTmux):
    fake_tmux.queue_pane(CC_IDLE)  # ready poll → is_ready=True

    hs = _make_session(ClaudeCodeAdapter())
    result = asyncio.run(hs.start(_start_spec()))

    assert result.ok
    names = fake_tmux.call_names()
    assert "create_session" in names


def test_start_success_passes_correct_cmd_to_create_session(fake_tmux: FakeTmux):
    fake_tmux.queue_pane(CC_IDLE)
    hs = _make_session(ClaudeCodeAdapter())
    asyncio.run(hs.start(_start_spec()))

    create_calls = fake_tmux.calls_to("create_session")
    assert len(create_calls) == 1
    _args, _kw = create_calls[0]
    session_name, _cwd, cmd = _args
    assert session_name == "test-session"
    assert "claude" in cmd
    assert "--dangerously-skip-permissions" in cmd


def test_start_success_polls_capture_pane_for_ready(fake_tmux: FakeTmux):
    # startup_ready loop must call capture_pane at least once
    fake_tmux.queue_pane(CC_IDLE)
    hs = _make_session(ClaudeCodeAdapter())
    asyncio.run(hs.start(_start_spec()))

    assert "capture_pane" in fake_tmux.call_names()


def test_legacy_prompt_api_is_not_exposed_by_session_or_adapter() -> None:
    """Verified control is the only prompt-delivery ownership boundary."""

    assert not hasattr(HarnessSession, "send_prompt")
    assert not hasattr(HarnessAdapter, "send_prompt")


# ============================================================
# === EDGE CASES =============================================
# ============================================================


# ── start() — failure paths ──────────────────────────────────────────────────


def test_start_ready_timeout_returns_fail_result(fake_tmux: FakeTmux):
    # Queue a non-ready pane; with ready_timeout_s=0.4 only 1 attempt is made
    fake_tmux.queue_pane("$ ")  # no CC banner → is_ready=False
    hs = _make_session(ClaudeCodeAdapter())

    result = asyncio.run(hs.start(_start_spec()))

    assert not result.ok
    assert result.message is not None
    assert "not ready in time" in result.message


def test_start_tmux_error_during_ready_poll_returns_fail_result(fake_tmux: FakeTmux):
    # TmuxError from capture_pane → "Session lost during startup"
    fake_tmux.queue_error("pane exited")
    hs = _make_session(ClaudeCodeAdapter())

    result = asyncio.run(hs.start(_start_spec()))

    assert not result.ok
    assert result.message is not None
    assert "Session lost during startup" in result.message


def test_start_with_requested_model_never_emits_legacy_model_control(fake_tmux: FakeTmux):
    fake_tmux.queue_pane(
        "  → Plan, search, build anything\n  Composer 2.5   Auto-run\n  ~/repo · main"
    )
    adapter = CursorAdapter(startup_model="gpt-5.5")
    hs = _make_session(adapter)

    asyncio.run(hs.start(_start_spec(model="gpt-5.5")))

    send_calls = fake_tmux.calls_to("send_keys")
    model_cmds = [args[1] for args, _ in send_calls if "/model" in args[1]]
    assert model_cmds == []


def test_cursor_startup_model_is_metadata_not_picker_input(fake_tmux: FakeTmux):
    fake_tmux.queue_pane(
        "  → Plan, search, build anything\n  Composer 2.5   Auto-run\n  ~/repo · main"
    )
    adapter = CursorAdapter(startup_model="composer-2.5")
    hs = _make_session(adapter)

    result = asyncio.run(hs.start(_start_spec(model="composer-2.5")))

    assert result.ok
    send_texts = [args[1] for args, _ in fake_tmux.calls_to("send_keys")]
    assert not any(text.startswith("/model") for text in send_texts)


# ── start() — startup model binding ─────────────────────────────────────────


def test_start_spec_model_is_metadata_not_startup_command(fake_tmux: FakeTmux):
    fake_tmux.queue_pane(CODEX_IDLE_MINI)
    hs = _make_session(CodexAdapter())

    result = asyncio.run(hs.start(_start_spec(model="gpt-5.4-mini")))

    assert result.ok
    create_calls = fake_tmux.calls_to("create_session")
    assert len(create_calls) == 1
    _session_name, _cwd, cmd = create_calls[0][0]
    assert "--model" not in cmd
    assert hs.adapter.startup_model == "gpt-5.4-mini"


def test_start_spec_additional_workspace_dirs_are_bound_before_startup_cmd(
    fake_tmux: FakeTmux,
):
    fake_tmux.queue_pane(CODEX_IDLE)
    hs = _make_session(CodexAdapter())
    ticket_dir = Path("/tmp/repo/.murder/tickets")

    result = asyncio.run(
        hs.start(
            HarnessStartSpec(
                cwd=Path("/tmp/repo/.murder/worktrees/crow/feature"),
                additional_workspace_dirs=(ticket_dir,),
                ready_timeout_s=0.4,
                poll_interval_s=0.4,
            )
        )
    )

    assert result.ok
    create_calls = fake_tmux.calls_to("create_session")
    assert len(create_calls) == 1
    _session_name, _cwd, cmd = create_calls[0][0]
    assert cmd[cmd.index("--add-dir") + 1] == str(ticket_dir)
    assert hs.adapter.additional_workspace_dirs == (ticket_dir,)


def test_legacy_session_and_adapter_model_control_apis_are_deleted() -> None:
    assert not hasattr(HarnessSession, "wait_ready")
    assert not hasattr(HarnessSession, "wait_idle")
    assert not hasattr(HarnessSession, "status_from_pane")
    assert not hasattr(HarnessSession, "collect_active_model_state")
    assert not hasattr(HarnessSession, "set_model")
    assert not hasattr(HarnessSession, "collect_available_models")
    assert not hasattr(HarnessSession, "probe_invalid_model")
    assert not hasattr(HarnessAdapter, "set_model")
    assert not hasattr(HarnessAdapter, "request_model_list")
    assert not hasattr(HarnessAdapter, "request_model_selection")
    assert not hasattr(HarnessSession, "initialize_defaults")
    assert not hasattr(HarnessSession, "request_usage_status")
    assert not hasattr(HarnessSession, "collect_usage_status")
    assert not hasattr(HarnessSession, "interrupt")
    assert not hasattr(HarnessAdapter, "initialize_defaults")
    assert not hasattr(HarnessAdapter, "request_usage_status")
    assert not hasattr(HarnessAdapter, "interrupt")
    assert not hasattr(HarnessAdapter, "interrupt_generation")


# ── codex / antigravity completion-detection regressions (BUG-11 / BUG-12) ────
#
# Live-captured fixtures: codex 0.142.0 and antigravity 1.0.10 drifted from the
# versions the adapters' regexes were first tuned against. Two HIGH-severity
# render bugs resulted — codex final replies never sealed (read busy forever on
# an idle frame) and antigravity's spinner never cleared (busy verb mismatch +
# spinner-text leak). These pin the corrected idle/busy detection.

CODEX_IDLE_AFTER_PROSE = _load("codex_idle_after_prose_narration.txt")
AGY_BUSY_LOADING = _load("agy_busy_loading.txt")


def test_codex_idle_completion_frame_with_running_prose_reads_idle():
    """BUG-11: a COMPLETED codex turn sits at the `›` prompt but its assistant
    narration opened with `• Running the requested shell command…`. The old
    verb-list _BUSY_RE matched that prose and kept the pane "busy" forever, so
    the final reply never sealed/delivered until the next turn. The genuine busy
    spinner always carries "esc to interrupt"; this idle frame has none."""
    adapter = CodexAdapter()
    assert adapter.is_busy(CODEX_IDLE_AFTER_PROSE) is False
    assert adapter.is_idle(CODEX_IDLE_AFTER_PROSE) is True


def test_codex_busy_spinner_still_reads_busy():
    """The real codex working spinner — verified across recorded busy fixtures —
    always renders the "esc to interrupt" hint and must read busy."""
    adapter = CodexAdapter()
    pane = "• Working (3s • esc to interrupt)\n\n›\n  gpt-5.4-mini medium · ~/repo"
    assert adapter.is_busy(pane) is True
    assert adapter.is_idle(pane) is False


def test_antigravity_loading_spinner_reads_busy():
    """BUG-12: antigravity 1.0.10 paints "Loading..." (not 1.0.2's "Generating...")
    while generating; the modal footer "esc to cancel" is the version-stable busy
    marker. The pane must read busy (and not idle) so the spinner state clears
    correctly when it later returns to "? for shortcuts"."""
    adapter = AntigravityAdapter()
    assert adapter.is_busy(AGY_BUSY_LOADING) is True
    assert adapter.is_idle(AGY_BUSY_LOADING) is False
