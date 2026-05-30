"""Tests for ``HarnessSession`` — the live-session facade in ``harnesses/base.py``.

All tmux I/O is intercepted via ``FakeTmux`` (see ``tests/support/fake_tmux.py``).
``asyncio.sleep`` is patched to a no-op so timing delays don't slow tests.
Tests use ``asyncio.run()`` directly (consistent with the rest of the test suite).

Coverage goals per the plan:
  - start() success — create_session called; startup_ready gate; configure path;
    _first_send_idle_gate_pending set to True
  - start() ready timeout — fail_result with "not ready in time"
  - start() TmuxError during ready poll — fail_result with "Session lost during startup"
  - start() set_model failure propagates
  - First send_prompt waits for idle; subsequent sends skip the wait
  - wait_idle timeout → fail_result
  - wait_idle TmuxError → fail_result "Session lost during idle-wait"
  - interrupt() delegates to tmux.interrupt
  - set_model() on non-runtime-selectable adapter returns fail_result
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import murder.terminal.tmux as tmux_mod
from murder.harnesses.base import HarnessSession
from murder.harnesses.claude_code import ClaudeCodeAdapter
from murder.harnesses.codex import CodexAdapter
from murder.harnesses.cursor import CursorAdapter
from murder.harnesses.models import HarnessStartSpec
from tests.support.fake_tmux import FakeTmux

# ── Pane texts loaded from fixtures ──────────────────────────────────────────

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "harness_panes"


def _load(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding="utf-8")


CC_IDLE = _load("cc_idle.txt")
CC_BUSY = _load("cc_busy.txt")


# ── Shared fixture / helpers ──────────────────────────────────────────────────


@pytest.fixture
def fake_tmux(monkeypatch):
    ft = FakeTmux()
    ft.install(monkeypatch, tmux_mod)

    async def _noop_sleep(_: float = 0) -> None:
        pass

    monkeypatch.setattr("asyncio.sleep", _noop_sleep)
    return ft


def _start_spec(cwd: Path = Path("/tmp/test-repo"), *, model: str | None = None) -> HarnessStartSpec:
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


# ─────────────────────────────────────────────────────────────────────────────
# start() — success path
# ─────────────────────────────────────────────────────────────────────────────


def test_start_success_creates_tmux_session(fake_tmux: FakeTmux):
    fake_tmux.queue_pane(CC_IDLE)  # ready poll → is_ready=True
    # initialize_defaults polls capture_pane until is_idle; CC_IDLE is idle
    # wait_idle at end of _configure_started_session also uses CC_IDLE (repeated)

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


def test_start_success_sets_first_send_idle_gate(fake_tmux: FakeTmux):
    fake_tmux.queue_pane(CC_IDLE)
    hs = _make_session(ClaudeCodeAdapter())

    assert hs._first_send_idle_gate_pending is False
    asyncio.run(hs.start(_start_spec()))
    assert hs._first_send_idle_gate_pending is True


def test_start_success_polls_capture_pane_for_ready(fake_tmux: FakeTmux):
    # startup_ready loop must call capture_pane at least once
    fake_tmux.queue_pane(CC_IDLE)
    hs = _make_session(ClaudeCodeAdapter())
    asyncio.run(hs.start(_start_spec()))

    assert "capture_pane" in fake_tmux.call_names()


# ─────────────────────────────────────────────────────────────────────────────
# start() — failure paths
# ─────────────────────────────────────────────────────────────────────────────


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


def test_start_with_startup_model_calls_set_model(fake_tmux: FakeTmux):
    # Cursor supports runtime model selection via set_model → True
    fake_tmux.queue_pane("  → Plan, search, build anything\n  Composer 2.5   Auto-run\n  ~/repo · main")
    adapter = CursorAdapter(startup_model="gpt-5.5")
    hs = _make_session(adapter)

    result = asyncio.run(hs.start(_start_spec(model="gpt-5.5")))

    # set_model for Cursor calls send_keys with "/model gpt-5.5"
    send_calls = fake_tmux.calls_to("send_keys")
    model_cmds = [args[1] for args, _ in send_calls if "/model" in args[1]]
    assert len(model_cmds) >= 1, "Expected at least one /model command"


def test_start_set_model_fail_returns_fail_result(fake_tmux: FakeTmux):
    # Unknown CC model ids fail before the startup prompt is sent.
    fake_tmux.queue_pane(CC_IDLE)
    adapter = ClaudeCodeAdapter()
    hs = _make_session(adapter)
    spec = HarnessStartSpec(
        cwd=Path("/tmp/repo"),
        startup_model="not-a-claude-model",
        ready_timeout_s=0.4,
        poll_interval_s=0.4,
    )

    result = asyncio.run(hs.start(spec))

    assert not result.ok
    assert "failed to select runtime model" in (result.message or "")


# ─────────────────────────────────────────────────────────────────────────────
# send_prompt() — idle gate
# ─────────────────────────────────────────────────────────────────────────────


def test_first_send_prompt_waits_for_idle(fake_tmux: FakeTmux):
    # After start(), _first_send_idle_gate_pending=True; first send_prompt
    # must call capture_pane before send_keys.
    fake_tmux.queue_pane(CC_IDLE)
    hs = _make_session(ClaudeCodeAdapter())
    asyncio.run(hs.start(_start_spec()))
    fake_tmux.calls.clear()

    fake_tmux.queue_pane(CC_IDLE)  # wait_idle poll
    asyncio.run(hs.send_prompt("hello world"))

    names = fake_tmux.call_names()
    # capture_pane (idle check) must precede send_keys
    assert "capture_pane" in names
    assert "send_keys" in names
    assert names.index("capture_pane") < names.index("send_keys")


def test_first_send_prompt_clears_gate(fake_tmux: FakeTmux):
    fake_tmux.queue_pane(CC_IDLE)
    hs = _make_session(ClaudeCodeAdapter())
    asyncio.run(hs.start(_start_spec()))

    fake_tmux.queue_pane(CC_IDLE)
    asyncio.run(hs.send_prompt("first"))

    assert hs._first_send_idle_gate_pending is False


def test_second_send_prompt_skips_idle_poll(fake_tmux: FakeTmux):
    fake_tmux.queue_pane(CC_IDLE)
    hs = _make_session(ClaudeCodeAdapter())
    asyncio.run(hs.start(_start_spec()))

    fake_tmux.queue_pane(CC_IDLE)  # for first send idle gate
    asyncio.run(hs.send_prompt("first"))
    fake_tmux.calls.clear()

    # Second send — gate is already cleared, no capture_pane call expected
    asyncio.run(hs.send_prompt("second"))

    names = fake_tmux.call_names()
    assert "send_keys" in names
    assert "capture_pane" not in names


def test_send_prompt_without_start_skips_gate(fake_tmux: FakeTmux):
    # If start() was never called, gate is False → send_prompt goes straight to send_keys
    hs = _make_session(ClaudeCodeAdapter())

    asyncio.run(hs.send_prompt("hello"))

    names = fake_tmux.call_names()
    assert "send_keys" in names
    assert "capture_pane" not in names


def test_send_prompt_passes_text_to_send_keys(fake_tmux: FakeTmux):
    hs = _make_session(ClaudeCodeAdapter())

    asyncio.run(hs.send_prompt("implement feature X"))

    send_calls = fake_tmux.calls_to("send_keys")
    texts = [args[1] for args, _ in send_calls]
    assert "implement feature X" in texts


def test_send_prompt_idle_timeout_returns_fail_result(fake_tmux: FakeTmux):
    # Gate pending but wait_idle times out (only 1 attempt with 0.4s timeout)
    fake_tmux.queue_pane(CC_IDLE)
    hs = _make_session(ClaudeCodeAdapter())
    asyncio.run(hs.start(_start_spec()))

    # Clear the residual CC_IDLE in the queue; replace with CC_BUSY so every
    # capture_pane during wait_idle(timeout_s=15.0) returns a non-idle pane.
    fake_tmux.reset_queue()
    fake_tmux.queue_pane(CC_BUSY)
    result = asyncio.run(hs.send_prompt("test"))

    assert not result.ok
    assert "not idle in time" in (result.message or "")


# ─────────────────────────────────────────────────────────────────────────────
# wait_idle() — timeout and TmuxError branches
# ─────────────────────────────────────────────────────────────────────────────


def test_wait_idle_succeeds_on_idle_pane(fake_tmux: FakeTmux):
    hs = _make_session(ClaudeCodeAdapter())
    fake_tmux.queue_pane(CC_IDLE)

    result = asyncio.run(hs.wait_idle(timeout_s=0.4))
    assert result.ok


def test_wait_idle_timeout_returns_fail_result(fake_tmux: FakeTmux):
    hs = _make_session(ClaudeCodeAdapter())
    fake_tmux.queue_pane(CC_BUSY)  # never idle

    result = asyncio.run(hs.wait_idle(timeout_s=0.4))

    assert not result.ok
    assert "not idle in time" in (result.message or "")


def test_wait_idle_tmux_error_returns_fail_result(fake_tmux: FakeTmux):
    hs = _make_session(ClaudeCodeAdapter())
    fake_tmux.queue_error("session gone")

    result = asyncio.run(hs.wait_idle(timeout_s=0.4))

    assert not result.ok
    assert "Session lost during idle-wait" in (result.message or "")


# ─────────────────────────────────────────────────────────────────────────────
# wait_ready()
# ─────────────────────────────────────────────────────────────────────────────


def test_wait_ready_succeeds_on_ready_pane(fake_tmux: FakeTmux):
    hs = _make_session(ClaudeCodeAdapter())
    fake_tmux.queue_pane(CC_IDLE)

    result = asyncio.run(hs.wait_ready(timeout_s=0.4))
    assert result.ok


def test_wait_ready_timeout_returns_fail_result(fake_tmux: FakeTmux):
    hs = _make_session(ClaudeCodeAdapter())
    fake_tmux.queue_pane("$ ")  # not ready

    result = asyncio.run(hs.wait_ready(timeout_s=0.4))

    assert not result.ok
    assert "not ready in time" in (result.message or "")


def test_wait_ready_tmux_error_returns_fail_result(fake_tmux: FakeTmux):
    hs = _make_session(ClaudeCodeAdapter())
    fake_tmux.queue_error()

    result = asyncio.run(hs.wait_ready(timeout_s=0.4))

    assert not result.ok
    assert "Session lost during ready-wait" in (result.message or "")


# ─────────────────────────────────────────────────────────────────────────────
# interrupt() — delegates to adapter (CC sends Escape, not Ctrl+C)
# ─────────────────────────────────────────────────────────────────────────────


def test_interrupt_calls_adapter_escape(fake_tmux: FakeTmux):
    hs = _make_session(ClaudeCodeAdapter())

    result = asyncio.run(hs.interrupt())

    assert result.ok
    send_calls = fake_tmux.calls_to("send_keys")
    assert len(send_calls) == 1
    (session_arg, keys), kw = send_calls[0]
    assert session_arg == "test-session"
    assert keys == "Escape"
    assert kw == {"literal": False, "enter": False}


# ─────────────────────────────────────────────────────────────────────────────
# set_model() — capability routing
# ─────────────────────────────────────────────────────────────────────────────


def test_set_model_fails_for_non_runtime_selectable_adapter(fake_tmux: FakeTmux):
    hs = _make_session(ClaudeCodeAdapter())

    result = asyncio.run(hs.set_model("not-a-claude-model"))

    assert not result.ok
    assert "failed to select runtime model" in (result.message or "")


def test_set_model_succeeds_when_model_matches_startup(fake_tmux: FakeTmux):
    adapter = ClaudeCodeAdapter()
    hs = _make_session(adapter)
    fake_tmux.queue_pane(
        "Claude Code v2.1.150\nHaiku 4.5 with medium effort · Claude Pro\n❯ \n"
    )

    result = asyncio.run(hs.set_model("haiku"))

    assert result.ok


def test_set_model_cursor_sends_slash_model_command(fake_tmux: FakeTmux):
    fake_tmux.queue_pane(
        "  → Plan, search, build anything\n  GPT-5.5   Auto-run\n  ~/repo · main\n"
    )
    hs = _make_session(CursorAdapter())

    result = asyncio.run(hs.set_model("gpt-5.5"))

    assert result.ok
    send_calls = fake_tmux.calls_to("send_keys")
    texts = [args[1] for args, _ in send_calls]
    assert any("/model gpt-5.5" in t for t in texts)


# ─────────────────────────────────────────────────────────────────────────────
# status_from_pane() — pure function, no tmux I/O
# ─────────────────────────────────────────────────────────────────────────────


def test_status_from_idle_pane(fake_tmux: FakeTmux):
    hs = _make_session(ClaudeCodeAdapter())
    state = hs.status_from_pane(CC_IDLE)

    assert state.ready is True
    assert state.idle is True
    assert state.busy is False


def test_status_from_busy_pane(fake_tmux: FakeTmux):
    hs = _make_session(ClaudeCodeAdapter())
    state = hs.status_from_pane(CC_BUSY)

    assert state.ready is True
    assert state.idle is False
    assert state.busy is True


# ─────────────────────────────────────────────────────────────────────────────
# initialize_defaults() — trust dialog and auto-run
# ─────────────────────────────────────────────────────────────────────────────


def test_cc_start_dismisses_trust_dialog(fake_tmux: FakeTmux):
    # When CC's first-run trust dialog is up, initialize_defaults() should
    # send "1" (select "Yes, I trust this folder") via send_keys.
    # source fixture: tools/testing/recordings/20260526-111559-claude-trust-dialog-haiku
    cc_trust = _load("cc_trust_dialog.txt")

    fake_tmux.queue_pane(cc_trust)  # _wait_startup_ready: is_ready=True (banner present)
    fake_tmux.queue_pane(cc_trust)  # initialize_defaults: first poll sees dialog
    fake_tmux.queue_pane(CC_IDLE)   # initialize_defaults: after "1", polls as idle
    # wait_idle at the end of _configure_started_session → CC_IDLE (repeated)

    hs = _make_session(ClaudeCodeAdapter())
    result = asyncio.run(hs.start(_start_spec()))

    assert result.ok
    send_texts = [args[1] for args, _ in fake_tmux.calls_to("send_keys")]
    assert "1" in send_texts, "Expected trust dialog acceptance ('1') to be sent"


def test_cursor_start_sends_auto_run_command(fake_tmux: FakeTmux):
    # CursorAdapter.initialize_defaults sends "/auto-run on" (spec.auto_run is None → "on")
    cursor_idle = _load("cursor_idle.txt")
    fake_tmux.queue_pane(cursor_idle)

    hs = _make_session(CursorAdapter())
    spec = _start_spec()
    asyncio.run(hs.start(spec))

    send_texts = [args[1] for args, _ in fake_tmux.calls_to("send_keys")]
    assert any("/auto-run" in t for t in send_texts), "Expected /auto-run command to be sent"


# ─────────────────────────────────────────────────────────────────────────────
# Codex send_prompt override — Tab+Enter submit path
# ─────────────────────────────────────────────────────────────────────────────


def test_codex_small_prompt_uses_send_keys_with_tab_enter(fake_tmux: FakeTmux):
    # Short prompt (<1024 bytes): send_keys text, then send_keys Enter
    adapter = CodexAdapter()
    hs = _make_session(adapter)

    asyncio.run(hs.adapter.send_prompt("test-session", "short prompt"))

    send_calls = fake_tmux.calls_to("send_keys")
    # The adapter sends the text first, then an empty Tab/Enter
    assert len(send_calls) >= 2
    texts = [args[1] for args, _ in send_calls]
    assert "short prompt" in texts


def test_codex_large_prompt_uses_paste_buffer_chunks(fake_tmux: FakeTmux):
    # Prompt ≥ 1024 bytes: split into 768-byte chunks via paste_buffer_literal
    adapter = CodexAdapter()
    hs = _make_session(adapter)

    large_prompt = "x" * 2048
    asyncio.run(hs.adapter.send_prompt("test-session", large_prompt))

    paste_calls = fake_tmux.calls_to("paste_buffer_literal")
    assert len(paste_calls) >= 2, "Expected multiple paste_buffer_literal calls for 2KB prompt"

    # Verify each chunk ≤ 768 bytes UTF-8
    for (_, chunk), _ in paste_calls:
        assert len(chunk.encode("utf-8")) <= 768


def test_codex_large_prompt_all_chunks_sum_to_original(fake_tmux: FakeTmux):
    adapter = CodexAdapter()
    large_prompt = "abc" * 400  # 1200 bytes
    asyncio.run(adapter.send_prompt("test-session", large_prompt))

    paste_calls = fake_tmux.calls_to("paste_buffer_literal")
    reassembled = "".join(chunk for (_, chunk), _ in paste_calls)
    assert reassembled == large_prompt
