"""Harness adapter pane-state predicate tests.

Each test loads a real pane fixture extracted from tmux session recordings
(see ``tools/testing/extract_fixtures.py``) and asserts the adapter's
``is_idle``, ``is_ready``, or ``is_busy`` return value.  Synthetic panes built
with ``PaneSimulator`` cover code paths not exercised by the available
recordings (edge cases, known adapter gaps).

Fixture provenance:
  cc_idle / cc_busy / cc_startup / cc_trust_dialog / cc_multiturn_idle
      → tools/testing/recordings/20260523-215258 and 20260526-*
  codex_idle / codex_busy / codex_startup
      → tools/testing/recordings/20260523-215413 and 20260526-122908-codex-busy-mini
  cursor_idle / cursor_busy / cursor_startup
      → tools/testing/recordings/20260523-215643 and 20260526-102216-cursor-busy
  pi_idle / pi_busy
      → tools/testing/recordings/20260523-215816 and 20260526-103015-pi-busy-deepseek-v4-flash
  agy_idle / agy_busy / agy_trust_dialog / agy_signing_in / agy_model_picker
      → tools/testing/recordings/20260527-21*-agy-*
"""

from __future__ import annotations

from pathlib import Path

import pytest

from murder.llm.harnesses.antigravity import AntigravityAdapter
from murder.llm.harnesses.claude_code import ClaudeCodeAdapter
from murder.llm.harnesses.codex import CodexAdapter
from murder.llm.harnesses.cursor import CursorAdapter
from murder.llm.harnesses.pi_harness import PiAdapter
from tests.support.simulators import PaneSimulator

FIXTURES = Path(__file__).parent.parent / "fixtures" / "harness_panes"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures as module-level constants (avoid repeated I/O)
# ─────────────────────────────────────────────────────────────────────────────

CC_IDLE = _load("cc_idle.txt")
CC_BUSY = _load("cc_busy.txt")
CC_STARTUP = _load("cc_startup.txt")
CC_TRUST_DIALOG = _load("cc_trust_dialog.txt")
CC_MULTITURN = _load("cc_multiturn_idle.txt")

CODEX_IDLE = _load("codex_idle.txt")
CODEX_BUSY = _load("codex_busy.txt")
CODEX_STARTUP = _load("codex_startup.txt")

CURSOR_IDLE = _load("cursor_idle.txt")
CURSOR_BUSY = _load("cursor_busy.txt")
CURSOR_STARTUP = _load("cursor_startup.txt")

PI_IDLE = _load("pi_idle.txt")
PI_BUSY = _load("pi_busy.txt")

AGY_IDLE = _load("agy_idle.txt")
AGY_BUSY = _load("agy_busy.txt")
AGY_TRUST_DIALOG = _load("agy_trust_dialog.txt")
AGY_SIGNING_IN = _load("agy_signing_in.txt")
AGY_MODEL_PICKER = _load("agy_model_picker.txt")


# ─────────────────────────────────────────────────────────────────────────────
# ClaudeCode adapter
# ─────────────────────────────────────────────────────────────────────────────

class TestClaudeCodeAdapter:
    cc = ClaudeCodeAdapter()

    # ── idle / ready / busy on the idle fixture ───────────────────────────────

    def test_idle_pane_is_idle(self):
        # CC 2.x: bypass permissions present, esc to interrupt absent → idle
        assert self.cc.is_idle(CC_IDLE) is True

    def test_idle_pane_is_ready(self):
        assert self.cc.is_ready(CC_IDLE) is True

    def test_idle_pane_not_busy(self):
        assert self.cc.is_busy(CC_IDLE) is False

    # ── busy fixture (esc to interrupt present) ───────────────────────────────

    def test_busy_pane_is_busy(self):
        # Recording shows "esc to interrupt" in the status bar while generating
        assert self.cc.is_busy(CC_BUSY) is True

    def test_busy_pane_not_idle(self):
        assert self.cc.is_idle(CC_BUSY) is False

    def test_busy_pane_is_ready(self):
        # CC stays "ready" (banner present) even while busy
        assert self.cc.is_ready(CC_BUSY) is True

    # ── startup fixture ───────────────────────────────────────────────────────

    def test_startup_pane_is_ready(self):
        # "Claude Code v2" banner is present immediately after launch
        assert self.cc.is_ready(CC_STARTUP) is True

    # ── trust dialog (first-run, untrusted directory) ─────────────────────────

    def test_trust_dialog_is_ready(self):
        # "Claude Code'll be able to read…" contains the banner keyword
        assert self.cc.is_ready(CC_TRUST_DIALOG) is True

    def test_trust_dialog_not_idle(self):
        # No "bypass permissions" → falls through to bare-prompt check;
        # no bare ❯/>/? prompt at end of line → not idle
        assert self.cc.is_idle(CC_TRUST_DIALOG) is False

    # ── multiturn idle fixture (2 completed turns + live empty cursor) ────────

    def test_multiturn_idle_pane_is_idle(self):
        assert self.cc.is_idle(CC_MULTITURN) is True

    def test_multiturn_idle_pane_not_busy(self):
        assert self.cc.is_busy(CC_MULTITURN) is False

    # ── synthetic edge cases ──────────────────────────────────────────────────

    def test_bare_prompt_without_cc2_header_is_idle(self):
        # CC 1.x / unknown variant: bare > prompt at end of pane
        pane = PaneSimulator().add("Some output", "> ").render()
        assert self.cc.is_idle(pane) is True

    def test_thinking_word_triggers_busy(self):
        # "thinking" keyword (CC 1.x style) is matched by _BUSY_RE
        pane = PaneSimulator().add("● Thinking about your request…").render()
        assert self.cc.is_busy(pane) is True

    def test_startup_cmd_includes_skip_permissions_flag(self):
        adapter = ClaudeCodeAdapter()
        cmd = adapter.startup_cmd(Path("/tmp/repo"))
        assert "claude" in cmd
        assert "--dangerously-skip-permissions" in cmd

    def test_startup_cmd_does_not_include_model_flag(self):
        adapter = ClaudeCodeAdapter(startup_model="haiku")
        cmd = adapter.startup_cmd(Path("/tmp/repo"))
        assert "--model" not in cmd
        assert "haiku" not in cmd


# ─────────────────────────────────────────────────────────────────────────────
# Codex adapter
# ─────────────────────────────────────────────────────────────────────────────

class TestCodexAdapter:
    cx = CodexAdapter()

    # ── idle fixture ──────────────────────────────────────────────────────────

    def test_idle_pane_is_idle(self):
        assert self.cx.is_idle(CODEX_IDLE) is True

    def test_idle_pane_is_ready(self):
        assert self.cx.is_ready(CODEX_IDLE) is True

    def test_idle_pane_not_busy(self):
        assert self.cx.is_busy(CODEX_IDLE) is False

    # ── startup fixture ───────────────────────────────────────────────────────

    def test_startup_pane_is_ready(self):
        # Banner "OpenAI Codex" present immediately
        assert self.cx.is_ready(CODEX_STARTUP) is True

    def test_startup_pane_is_idle(self):
        # The › prompt is visible; "Starting MCP servers" line doesn't match _BUSY_RE
        assert self.cx.is_idle(CODEX_STARTUP) is True

    def test_startup_cmd_passes_requested_model(self):
        cmd = CodexAdapter(startup_model="gpt-5.4-mini").startup_cmd(Path("/tmp/repo"))
        assert cmd[cmd.index("--model") + 1] == "gpt-5.4-mini"

    # ── "busy" fixture — known gap: • Working is not detected ─────────────────

    def test_codex_bullet_working_is_busy(self):
        # "• Working (0s • esc to interrupt)" is detected via bullet-aware _BUSY_RE
        assert self.cx.is_busy(CODEX_BUSY) is True

    def test_codex_busy_pane_not_idle(self):
        # is_busy detects "• Working"; is_idle() correctly returns False
        assert self.cx.is_idle(CODEX_BUSY) is False

    # ── synthetic: indented "working" line IS detected ────────────────────────

    def test_indented_working_triggers_busy(self):
        # Whitespace-only prefix satisfies ``^\s*working``
        pane = (
            PaneSimulator()
            .add(
                "OpenAI Codex",
                "",
                "  working on your request…",
                "",
                "› Explain this codebase",
                "  gpt-5.4 high · ~/project",
            )
            .render()
        )
        assert self.cx.is_busy(pane) is True

    def test_indented_working_pane_not_idle(self):
        pane = (
            PaneSimulator()
            .add(
                "OpenAI Codex",
                "",
                "  working on your request…",
                "",
                "› placeholder",
                "  gpt-5.4 high · ~/project",
            )
            .render()
        )
        assert self.cx.is_idle(pane) is False

    def test_login_required_pane_not_idle(self):
        pane = PaneSimulator().add("› placeholder", "  login required").render()
        assert self.cx.is_idle(pane) is False

    def test_login_required_pane_not_ready(self):
        pane = PaneSimulator().add("OpenAI Codex", "  login required").render()
        assert self.cx.is_ready(pane) is False


# ─────────────────────────────────────────────────────────────────────────────
# Cursor adapter
# ─────────────────────────────────────────────────────────────────────────────

class TestCursorAdapter:
    cu = CursorAdapter()

    # ── idle fixture ──────────────────────────────────────────────────────────

    def test_idle_pane_is_idle(self):
        assert self.cu.is_idle(CURSOR_IDLE) is True

    def test_idle_pane_is_ready(self):
        assert self.cu.is_ready(CURSOR_IDLE) is True

    def test_idle_pane_not_busy(self):
        assert self.cu.is_busy(CURSOR_IDLE) is False

    # ── startup fixture (same shape as idle for Cursor) ───────────────────────

    def test_startup_pane_is_ready(self):
        assert self.cu.is_ready(CURSOR_STARTUP) is True

    # ── busy fixture ──────────────────────────────────────────────────────────

    def test_busy_pane_is_busy_via_ctrl_c(self):
        # "ctrl+c to stop" appears in the input box during generation
        assert self.cu.is_busy(CURSOR_BUSY) is True

    def test_busy_pane_is_busy_via_spinner(self):
        # ⠀⠞ Composing line also triggers _BUSY_SPINNER_RE
        assert self.cu.is_busy(CURSOR_BUSY) is True

    def test_busy_pane_not_idle(self):
        # "ctrl+c to stop" in tail → is_idle returns False despite placeholder
        assert self.cu.is_idle(CURSOR_BUSY) is False

    def test_busy_pane_is_ready(self):
        # "Add a follow-up" is in the tail even during generation
        assert self.cu.is_ready(CURSOR_BUSY) is True

    # ── synthetic edge cases ──────────────────────────────────────────────────

    def test_add_follow_up_placeholder_is_idle(self):
        pane = PaneSimulator().add("  → Add a follow-up").render()
        assert self.cu.is_idle(pane) is True

    def test_plan_search_placeholder_is_idle(self):
        pane = PaneSimulator().add("  → Plan, search, build anything").render()
        assert self.cu.is_idle(pane) is True

    def test_trust_prompt_blocks_ready(self):
        pane = PaneSimulator().add("⚠ Workspace Trust Required", "→ Add a follow-up").render()
        assert self.cu.is_ready(pane) is False

    def test_composing_spinner_triggers_busy(self):
        pane = (
            PaneSimulator()
            .add(" ⠞ Composing", "  → Add a follow-up", "  Composer 2.5   Auto-run")
            .render()
        )
        assert self.cu.is_busy(pane) is True


# ─────────────────────────────────────────────────────────────────────────────
# Pi adapter
# ─────────────────────────────────────────────────────────────────────────────

class TestPiAdapter:
    pi = PiAdapter()

    # ── idle fixture (has 0.0%/66k context gauge in tail) ─────────────────────

    def test_idle_pane_is_idle(self):
        assert self.pi.is_idle(PI_IDLE) is True

    def test_idle_pane_is_ready(self):
        assert self.pi.is_ready(PI_IDLE) is True

    def test_idle_pane_not_busy(self):
        assert self.pi.is_busy(PI_IDLE) is False

    # ── busy fixture ──────────────────────────────────────────────────────────

    def test_busy_pane_is_busy(self):
        # "⠼ Working..." spinner line is correctly matched by _BUSY_RE via the
        # "working" keyword. The word "running" in prose also still satisfies it.
        assert self.pi.is_busy(PI_BUSY) is True

    def test_busy_pane_not_idle(self):
        assert self.pi.is_idle(PI_BUSY) is False

    # ── synthetic: explicit busy words ───────────────────────────────────────

    def test_thinking_word_triggers_busy(self):
        pane = (
            PaneSimulator()
            .add(">", "0.0%/66k (auto)  model • medium", "", " thinking about it…")
            .render()
        )
        assert self.pi.is_busy(pane) is True

    def test_streaming_word_triggers_busy(self):
        pane = (
            PaneSimulator()
            .add(">", "0.0%/66k (auto)  model • medium", "", " streaming response")
            .render()
        )
        assert self.pi.is_busy(pane) is True

    def test_context_gauge_1m_is_idle(self):
        # Pi large-context models show "0.0%/1.0M"; _IDLE_RE now matches M-scale gauges
        pane = (
            PaneSimulator()
            .add(
                " Agent is processing…",
                "~/project",
                "0.0%/1.0M (auto)    (or) deepseek/deepseek-v4-flash • medium",
            )
            .render()
        )
        assert self.pi.is_idle(pane) is True

    def test_context_gauge_k_is_idle(self):
        # Standard k-scale context gauge is matched by _IDLE_RE
        pane = PaneSimulator().add(">", "0.0%/128k (auto)  model • medium").render()
        assert self.pi.is_idle(pane) is True

    def test_auth_error_blocks_ready(self):
        pane = PaneSimulator().add("login required", ">").render()
        assert self.pi.is_ready(pane) is False


# ─────────────────────────────────────────────────────────────────────────────
# Antigravity adapter
# ─────────────────────────────────────────────────────────────────────────────

class TestAntigravityAdapter:
    agy = AntigravityAdapter()

    # ── idle fixture ──────────────────────────────────────────────────────────

    def test_idle_pane_is_idle(self):
        # "? for shortcuts" footer present, no Generating spinner → idle
        assert self.agy.is_idle(AGY_IDLE) is True

    def test_idle_pane_is_ready(self):
        assert self.agy.is_ready(AGY_IDLE) is True

    def test_idle_pane_not_busy(self):
        assert self.agy.is_busy(AGY_IDLE) is False

    # ── busy fixture ──────────────────────────────────────────────────────────

    def test_busy_pane_is_busy(self):
        # "⢿ Generating..." spinner line is detected via the static text
        assert self.agy.is_busy(AGY_BUSY) is True

    def test_busy_pane_not_idle(self):
        assert self.agy.is_idle(AGY_BUSY) is False

    def test_busy_pane_is_ready(self):
        # Banner + "esc to cancel" footer keeps the harness ready while busy
        assert self.agy.is_ready(AGY_BUSY) is True

    # ── trust dialog (first-run, untrusted directory) ─────────────────────────

    def test_trust_dialog_is_ready(self):
        # is_ready must include the trust dialog so initialize_defaults
        # gets a chance to dismiss it
        assert self.agy.is_ready(AGY_TRUST_DIALOG) is True

    def test_trust_dialog_not_idle(self):
        # No "? for shortcuts" footer yet; the dialog must block idle
        assert self.agy.is_idle(AGY_TRUST_DIALOG) is False

    # ── signing-in splash ─────────────────────────────────────────────────────

    def test_signing_in_pane_not_ready(self):
        # "Signing in..." spinner means the harness has not booted yet
        assert self.agy.is_ready(AGY_SIGNING_IN) is False

    def test_signing_in_pane_not_idle(self):
        assert self.agy.is_idle(AGY_SIGNING_IN) is False

    # ── /model picker (modal open) ────────────────────────────────────────────

    def test_model_picker_is_ready(self):
        # Picker is modal: footer is "esc to cancel" but harness is alive
        assert self.agy.is_ready(AGY_MODEL_PICKER) is True

    def test_model_picker_not_idle(self):
        # No "? for shortcuts" footer while the modal is open → not idle
        assert self.agy.is_idle(AGY_MODEL_PICKER) is False

    def test_model_picker_not_busy(self):
        # "esc to cancel" alone is not a busy marker (no Generating...)
        assert self.agy.is_busy(AGY_MODEL_PICKER) is False

    # ── synthetic ─────────────────────────────────────────────────────────────

    def test_startup_cmd_does_not_include_model_flag(self):
        # agy 1.0.2 has no --model flag; startup_model is advisory only
        adapter = AntigravityAdapter(startup_model="gemini-3.1-pro")
        cmd = adapter.startup_cmd(Path("/tmp/repo"))
        assert "agy" in cmd
        assert "--dangerously-skip-permissions" in cmd
        assert "--model" not in cmd

    # NOTE: antigravity set_model now navigates the /model picker (see
    # test_harness_runtime_model_selection.test_antigravity_set_model_navigates_picker);
    # the old advisory string-match contract no longer applies.


# ─────────────────────────────────────────────────────────────────────────────
# Shared sentinel detection (base class, adapter-agnostic)
# ─────────────────────────────────────────────────────────────────────────────

class TestSentinelDetection:
    """detect_ask / detect_done / detect_checks / detect_notes use base regexes
    on any adapter; tested here via ClaudeCodeAdapter as representative."""

    cc = ClaudeCodeAdapter()

    def test_detect_ask(self):
        # Terminate with another >>> marker so ASK_RE lookahead bounds the body
        pane = "Some output\n>>> ASK: what is the build command?\n>>> DONE"
        result = self.cc.detect_ask(pane)
        assert result == "what is the build command?"

    def test_detect_ask_none_when_absent(self):
        assert self.cc.detect_ask("no sentinel here") is None

    def test_detect_done(self):
        assert self.cc.detect_done(">>> DONE\n") is True

    def test_detect_done_false_when_absent(self):
        assert self.cc.detect_done("still working") is False

    def test_detect_checks(self):
        pane = ">>> CHECK: run tests\n>>> CHECK: push branch\n"
        checks = self.cc.detect_checks(pane)
        assert checks == ["run tests", "push branch"]

    def test_detect_notes(self):
        pane = ">>> NOTE: important context here\n>>> END\n"
        notes = self.cc.detect_notes(pane)
        assert notes == ["important context here"]

    def test_detect_asks_multiple(self):
        pane = ">>> ASK: first question\n>>> ASK: second question\n"
        asks = self.cc.detect_asks(pane)
        assert len(asks) == 2
        assert asks[0] == "first question"
        assert asks[1] == "second question"
