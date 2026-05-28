"""Tests for harness transcript parsing.

``parse_transcript`` on each adapter returns ``(role, text)`` turns extracted
from a raw pane capture.  Tests here pin the current parser behaviour against:

  cc_multiturn_idle.txt
      Two completed CC turns (create hello.py / update it) plus a live empty
      cursor.  Source: tools/testing/recordings/20260526-103649-claude-multiturn-haiku

Synthetic panes built with ``PaneSimulator`` cover the codex / pi prompt-marker
path and the cursor full-width user-line path without requiring multi-turn
recordings.
"""

from __future__ import annotations

from pathlib import Path

from murder.harnesses.claude_code import ClaudeCodeAdapter
from murder.harnesses.codex import CodexAdapter
from murder.harnesses.cursor import CursorAdapter
from murder.harnesses.pi_harness import PiAdapter
from murder.harnesses.parsing import parse_prompt_marker_transcript
from tests.support.simulators import PaneSimulator

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "harness_panes"


def _load(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding="utf-8")


CC_MULTITURN = _load("cc_multiturn_idle.txt")


# ─────────────────────────────────────────────────────────────────────────────
# ClaudeCode adapter — real multi-turn fixture
# ─────────────────────────────────────────────────────────────────────────────


class TestClaudeCodeTranscript:
    cc = ClaudeCodeAdapter()

    def test_multiturn_returns_four_turns(self):
        # 2 completed prompts → user1, assistant1, user2, assistant2
        turns = self.cc.parse_transcript(CC_MULTITURN)
        assert len(turns) == 4

    def test_multiturn_first_turn_is_user(self):
        turns = self.cc.parse_transcript(CC_MULTITURN)
        assert turns[0][0] == "user"

    def test_multiturn_first_user_text(self):
        turns = self.cc.parse_transcript(CC_MULTITURN)
        assert turns[0][1] == "Create a file hello.py that prints Hello from murder harness test"

    def test_multiturn_second_turn_is_assistant(self):
        turns = self.cc.parse_transcript(CC_MULTITURN)
        assert turns[1][0] == "assistant"

    def test_multiturn_assistant_reply_contains_write(self):
        turns = self.cc.parse_transcript(CC_MULTITURN)
        assert "Write(hello.py)" in turns[1][1] or "write" in turns[1][1].lower()

    def test_multiturn_third_turn_is_user(self):
        turns = self.cc.parse_transcript(CC_MULTITURN)
        assert turns[2][0] == "user"

    def test_multiturn_third_user_text(self):
        turns = self.cc.parse_transcript(CC_MULTITURN)
        assert turns[2][1] == "Now change it to print Hello twice and show me the file contents"

    def test_multiturn_fourth_turn_is_assistant(self):
        turns = self.cc.parse_transcript(CC_MULTITURN)
        assert turns[3][0] == "assistant"

    def test_multiturn_live_empty_cursor_not_emitted(self):
        # The final empty ❯ (live input box) must not produce a "user" turn
        turns = self.cc.parse_transcript(CC_MULTITURN)
        user_turns = [t for t in turns if t[0] == "user"]
        for _role, body in user_turns:
            assert body.strip() != "", "Empty user turn emitted from live cursor"

    def test_multiturn_no_chrome_in_assistant_body(self):
        turns = self.cc.parse_transcript(CC_MULTITURN)
        assistant_bodies = [body for role, body in turns if role == "assistant"]
        for body in assistant_bodies:
            assert "bypass permissions" not in body.lower()
            assert "esc to interrupt" not in body.lower()

    def test_multiturn_spinner_lines_dropped(self):
        # "✻ Sautéed for 3s" / "✻ Churned for 3s" must not appear in output
        turns = self.cc.parse_transcript(CC_MULTITURN)
        all_text = " ".join(body for _, body in turns)
        assert "Sautéed" not in all_text
        assert "Churned" not in all_text

    def test_empty_pane_returns_empty_list(self):
        assert self.cc.parse_transcript("") == []

    def test_no_prompt_pane_returns_empty_list(self):
        # Banner only (no ❯ prompt yet) → no turns
        banner = (
            " ▐▛███▜▌   Claude Code v2.1.150\n"
            "▝▜█████▛▘  Sonnet 4.6 · Claude Pro\n"
        )
        assert self.cc.parse_transcript(banner) == []


# ─────────────────────────────────────────────────────────────────────────────
# Codex adapter — synthetic pane (prompt-marker parser)
# ─────────────────────────────────────────────────────────────────────────────


class TestCodexTranscript:
    cx = CodexAdapter()

    def _make_pane(self) -> str:
        return (
            PaneSimulator()
            .add(
                "OpenAI Codex (v0.133.0)",
                "model: gpt-5.4 high · ~/project",
                "",
                "› write a hello world script",
                "",
                "• Sure! Here is hello.py:",
                "• print('hello world')",
                "",
                "› now add a shebang line",
                "",
                "• Added shebang:",
                "• #!/usr/bin/env python3",
                "",
                "› Explain this codebase",
                "  gpt-5.4 high · ~/project",
            )
            .render()
        )

    def test_two_completed_turns(self):
        turns = self.cx.parse_transcript(self._make_pane())
        assert len(turns) == 4  # user1, assistant1, user2, assistant2

    def test_first_user_text(self):
        turns = self.cx.parse_transcript(self._make_pane())
        assert turns[0] == ("user", "write a hello world script")

    def test_second_user_text(self):
        turns = self.cx.parse_transcript(self._make_pane())
        assert turns[2] == ("user", "now add a shebang line")

    def test_assistant_body_present(self):
        turns = self.cx.parse_transcript(self._make_pane())
        assert turns[1][0] == "assistant"
        assert turns[1][1].strip() != ""

    def test_placeholder_prompt_not_emitted(self):
        # The live "› Explain this codebase" input box → dangling prompt → discarded
        turns = self.cx.parse_transcript(self._make_pane())
        user_texts = [body for role, body in turns if role == "user"]
        assert "Explain this codebase" not in user_texts

    def test_banner_line_not_in_output(self):
        turns = self.cx.parse_transcript(self._make_pane())
        all_text = " ".join(body for _, body in turns)
        assert "OpenAI Codex" not in all_text

    def test_status_bar_not_in_output(self):
        turns = self.cx.parse_transcript(self._make_pane())
        all_text = " ".join(body for _, body in turns)
        assert "gpt-5.4 high · ~/project" not in all_text


# ─────────────────────────────────────────────────────────────────────────────
# Pi adapter — synthetic pane (PreprocessedPromptMarkerParser)
# ─────────────────────────────────────────────────────────────────────────────


class TestPiTranscript:
    pi = PiAdapter()

    def _make_pane(self) -> str:
        return (
            PaneSimulator()
            .add(
                " pi v0.74.1",
                " escape interrupt · ctrl+c/ctrl+d clear/exit",
                "",
                "> what is 2+2?",
                "",
                " 2+2 equals 4.",
                "",
                "> thank you",
                "",
                " You're welcome!",
                "",
                ">",
                "",
                "~/project",
                "0.0%/66k (auto)  model • medium",
            )
            .render()
        )

    def test_two_completed_turns(self):
        turns = self.pi.parse_transcript(self._make_pane())
        assert len(turns) == 4

    def test_first_user_text(self):
        turns = self.pi.parse_transcript(self._make_pane())
        assert turns[0] == ("user", "what is 2+2?")

    def test_second_user_text(self):
        turns = self.pi.parse_transcript(self._make_pane())
        assert turns[2] == ("user", "thank you")

    def test_bare_prompt_not_emitted(self):
        # Bare "> " live input box must be discarded
        turns = self.pi.parse_transcript(self._make_pane())
        user_texts = [body for role, body in turns if role == "user"]
        assert "" not in user_texts

    def test_pi_chrome_dropped(self):
        turns = self.pi.parse_transcript(self._make_pane())
        all_text = " ".join(body for _, body in turns)
        assert "pi v0.74.1" not in all_text
        assert "0.0%/66k" not in all_text
        assert "escape interrupt" not in all_text


# ─────────────────────────────────────────────────────────────────────────────
# Cursor adapter — synthetic pane (full-width user-line parser)
# ─────────────────────────────────────────────────────────────────────────────


class TestCursorTranscript:
    cu = CursorAdapter()

    def _make_pane(self, width: int = 120) -> str:
        # Cursor encodes user prompts as full-width padded lines (≥72 chars, ≥4 trailing spaces)
        def user_line(text: str) -> str:
            padded = text.ljust(width)
            return padded

        return (
            PaneSimulator()
            .add(
                "  Cursor Agent",
                "  v2026.05.20-2b5dd59",
                "",
                user_line("list all files in this repo"),
                "",
                "  The repo contains: README.md, main.py, tests/",
                "",
                user_line("summarize main.py"),
                "",
                "  main.py is an entry point.",
                "",
                "  → Add a follow-up",
                "  Composer 2.5   Auto-run",
                "  ~/project · main",
            )
            .render()
        )

    def test_two_completed_turns(self):
        turns = self.cu.parse_transcript(self._make_pane())
        assert len(turns) == 4

    def test_first_user_text(self):
        turns = self.cu.parse_transcript(self._make_pane())
        assert turns[0] == ("user", "list all files in this repo")

    def test_second_user_text(self):
        turns = self.cu.parse_transcript(self._make_pane())
        assert turns[2] == ("user", "summarize main.py")

    def test_chrome_not_in_output(self):
        turns = self.cu.parse_transcript(self._make_pane())
        all_text = " ".join(body for _, body in turns)
        assert "Cursor Agent" not in all_text
        assert "Add a follow-up" not in all_text
        assert "Composer 2.5" not in all_text


# ─────────────────────────────────────────────────────────────────────────────
# parse_prompt_marker_transcript — direct unit tests (adapter-agnostic)
# ─────────────────────────────────────────────────────────────────────────────


class TestParsePromptMarkerTranscript:
    def test_single_turn(self):
        pane = "❯ hello\nassistant reply\n❯ "
        turns = parse_prompt_marker_transcript(pane, prompt_markers=("❯",))
        assert turns == [("user", "hello"), ("assistant", "assistant reply")]

    def test_slash_command_echo_discarded(self):
        pane = "❯ /clear\n❯ what's next?\nassistant reply\n❯ "
        turns = parse_prompt_marker_transcript(pane, prompt_markers=("❯",))
        user_texts = [b for r, b in turns if r == "user"]
        assert "/clear" not in user_texts
        assert "what's next?" in user_texts

    def test_drop_substrings_filter_applied(self):
        pane = "❯ question\nstatus bar · ~/path\nreply text\n❯ "
        turns = parse_prompt_marker_transcript(
            pane, prompt_markers=("❯",), drop_substrings=(" · ~/",)
        )
        all_text = " ".join(b for _, b in turns)
        assert "status bar · ~/path" not in all_text

    def test_rule_lines_dropped(self):
        pane = "❯ hello\n────────\nreply\n❯ "
        turns = parse_prompt_marker_transcript(pane, prompt_markers=("❯",))
        all_text = " ".join(b for _, b in turns)
        assert "────" not in all_text

    def test_no_prompt_markers_returns_empty(self):
        pane = "some output\nmore output"
        turns = parse_prompt_marker_transcript(pane, prompt_markers=())
        assert turns == []

    def test_dangling_prompt_without_reply_not_emitted(self):
        # Final prompt with no subsequent reply → discarded
        pane = "❯ hello\nreply\n❯ still typing"
        turns = parse_prompt_marker_transcript(pane, prompt_markers=("❯",))
        user_texts = [b for r, b in turns if r == "user"]
        assert "still typing" not in user_texts

    def test_banner_before_first_prompt_dropped(self):
        pane = "Welcome banner\n❯ hello\nreply\n❯ "
        turns = parse_prompt_marker_transcript(pane, prompt_markers=("❯",))
        all_text = " ".join(b for _, b in turns)
        assert "Welcome banner" not in all_text
