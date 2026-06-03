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

from murder.harnesses.antigravity import AntigravityAdapter
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
CC_COLLAB_MULTILINE = _load("cc_collab_multiline.txt")
CURSOR_TOOL_OUTPUT = _load("cursor_tool_output.txt")


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

    def test_cc2_thinking_status_lines_dropped(self):
        # CC 2.x thinking spinners must be suppressed regardless of leading glyph
        # and separator style.  Two captures identical except one has the status
        # line must produce identical parsed turns (no flicker).
        pane_base = (
            "❯ do the thing\n"
            "\n"
            "● Running some tool\n"
            "  ⎿  result\n"
            "\n"
            "● Done.\n"
            "\n"
        )
        variants = [
            "* Recombobulating.. (8m 11s • ↓ 24.1k tokens)",
            "Recombobulating.. (8m 11s • ↓ 24.1k tokens)",
            "* Galloping... (47s · ↓ 2.4k tokens · thinking with medium effort)",
            "Galloping... (49s · ↓ 2.5k tokens · thinking with medium effort)",
        ]
        turns_base = self.cc.parse_transcript(pane_base)
        for line in variants:
            turns_with = self.cc.parse_transcript(pane_base + line + "\n")
            assert turns_with == turns_base, f"spinner line leaked into turns: {line!r}"
            all_text = " ".join(body for _, body in turns_with)
            assert "tokens" not in all_text, f"token count leaked for: {line!r}"

    def test_empty_pane_returns_empty_list(self):
        assert self.cc.parse_transcript("") == []

    def test_no_prompt_pane_returns_empty_list(self):
        # Banner only (no ❯ prompt yet) → no turns
        banner = (
            " ▐▛███▜▌   Claude Code v2.1.150\n"
            "▝▜█████▛▘  Sonnet 4.6 · Claude Pro\n"
        )
        assert self.cc.parse_transcript(banner) == []

    def test_gt_in_assistant_git_diff_not_a_user_turn(self):
        # > lines inside a git diff shown by CC must stay in the assistant turn,
        # not be split off as new user prompts.
        pane = (
            "❯ review the diff\n"
            "⏺ TodoRead(todo.md)\n"
            "  ⎿  read file\n"
            "\n"
            "● Here is the diff:\n"
            "\n"
            "> diff --git a/foo.py b/foo.py\n"
            "> @@ -1 +1 @@\n"
            "> -old line\n"
            "> +new line\n"
            "\n"
            "The change looks correct.\n"
            "\n"
            "❯ \n"
        )
        turns = self.cc.parse_transcript(pane)
        assert len(turns) == 2
        assert turns[0] == ("user", "review the diff")
        assert turns[1][0] == "assistant"
        assert "> diff --git" in turns[1][1]
        assert "The change looks correct." in turns[1][1]

    def test_gt_in_assistant_blockquote_not_a_user_turn(self):
        pane = (
            "❯ what did I say earlier?\n"
            "⏺ some tool\n"
            "● You said:\n"
            "\n"
            "> This is the quoted text\n"
            "\n"
            "That was your message.\n"
            "\n"
            "❯ \n"
        )
        turns = self.cc.parse_transcript(pane)
        assert len(turns) == 2
        assert turns[0][0] == "user"
        assert turns[1][0] == "assistant"
        assert "> This is the quoted text" in turns[1][1]


# ─────────────────────────────────────────────────────────────────────────────
# ClaudeCode adapter — multi-line brief fixture (the original bug)
# ─────────────────────────────────────────────────────────────────────────────


class TestClaudeCodeMultilineTranscript:
    """CC pane where the entire system prompt was sent as one message.

    CC echoes multi-line input with ❯ only on the first line; continuation
    lines are 2-space-indented.  The bug: the old prompt-marker parser treated
    all those continuation lines as the *assistant* body, so the collaborator
    turn contained the system-prompt tail + user message + actual response.
    """

    cc = ClaudeCodeAdapter()

    def test_assistant_turn_contains_only_the_response(self):
        turns = self.cc.parse_transcript(CC_COLLAB_MULTILINE)
        asst_bodies = [body for role, body in turns if role == "assistant"]
        assert len(asst_bodies) == 1
        assert "Hello! I'm ready to help" in asst_bodies[0]

    def test_system_prompt_tail_not_in_assistant(self):
        turns = self.cc.parse_transcript(CC_COLLAB_MULTILINE)
        asst_text = " ".join(body for role, body in turns if role == "assistant")
        assert "system is to generally assist" not in asst_text
        assert "Murder keeps state" not in asst_text
        assert "Ticket YAML" not in asst_text

    def test_user_message_not_in_assistant(self):
        turns = self.cc.parse_transcript(CC_COLLAB_MULTILINE)
        asst_text = " ".join(body for role, body in turns if role == "assistant")
        # "test" was the last line of the brief; it must not bleed into the
        # assistant turn (it may appear in the user turn, which is fine)
        assert asst_text.strip() == "Hello! I'm ready to help. What can I do for you?"

    def test_user_turn_present(self):
        turns = self.cc.parse_transcript(CC_COLLAB_MULTILINE)
        assert any(role == "user" for role, _ in turns)

    def test_two_turns_total(self):
        turns = self.cc.parse_transcript(CC_COLLAB_MULTILINE)
        assert len(turns) == 2  # 1 user + 1 assistant

    def test_bypass_permissions_not_in_output(self):
        turns = self.cc.parse_transcript(CC_COLLAB_MULTILINE)
        all_text = " ".join(body for _, body in turns)
        assert "bypass permissions" not in all_text.lower()


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

    def test_multiline_assistant_body_preserved(self):
        pane = (
            PaneSimulator()
            .add(
                "› reply with a short poem",
                "",
                "• first line",
                "• second line",
                "• third line",
                "",
                "› ",
            )
            .render()
        )
        turns = self.cx.parse_transcript(pane)
        assert turns == [
            ("user", "reply with a short poem"),
            ("assistant", "first line\nsecond line\nthird line"),
        ]


# ─────────────────────────────────────────────────────────────────────────────
# Pi adapter — synthetic pane (PreprocessedPromptMarkerParser)
# ─────────────────────────────────────────────────────────────────────────────


class TestPiTranscript:
    pi = PiAdapter()

    def _make_pane(self) -> str:
        # Real Pi (v0.74+) does not echo "> " on completed turns in scrollback.
        # Turns are plain 1-space-indented paragraphs separated by 2 blank lines;
        # a horizontal rule marks the end-of-session idle state.
        return (
            PaneSimulator()
            .add(
                " pi v0.74.1",
                " escape interrupt · ctrl+c/ctrl+d clear/exit",
                "",
                "",
                " what is 2+2?",
                "",
                "",
                " 2+2 equals 4.",
                "",
                "",
                " thank you",
                "",
                "",
                " You're welcome!",
                "",
                "─" * 80,
                "",
                "─" * 80,
                " ~/project",
                "0.0%/66k (auto)  deepseek/deepseek-v4-flash • medium",
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

    def test_no_empty_user_turns(self):
        # No blank user turns should appear.
        turns = self.pi.parse_transcript(self._make_pane())
        user_texts = [body for role, body in turns if role == "user"]
        assert all(t.strip() for t in user_texts)

    def test_pi_chrome_dropped(self):
        turns = self.pi.parse_transcript(self._make_pane())
        all_text = " ".join(body for _, body in turns)
        assert "pi v0.74.1" not in all_text
        assert "0.0%/66k" not in all_text
        assert "escape interrupt" not in all_text

    def test_wrapped_startup_chrome_fragments_dropped(self):
        pane = (
            PaneSimulator()
            .add(
                " pi v0.74.1",
                " warning: tmux extended-keys are not enabled; enable them and",
                " restart tmux.",
                " llama-3.2-1b-instruct-q4_k_m.g",
                " guf (Ctrl+P to cycle)",
                "",
                "",
                " reply with response text here",
                "",
                "",
                " response text here",
                "",
                "─" * 80,
                " ~/project",
                "0.0%/66k (auto)  deepseek/deepseek-v4-flash • medium",
            )
            .render()
        )

        turns = self.pi.parse_transcript(pane)

        assert turns == [
            ("user", "reply with response text here"),
            ("assistant", "response text here"),
        ]

    def test_internal_reasoning_prefix_removed_from_assistant_turns(self):
        pane = (
            PaneSimulator()
            .add(
                " reply with response text here",
                "",
                "",
                " The user wants me to reply with exactly the requested text.",
                "",
                " response text here",
                "",
                "",
                " ok",
                "",
                "",
                " I should answer with the short acknowledgement.",
                "",
                " ok",
                "",
                "─" * 80,
                " ~/project",
                "0.0%/66k (auto)  deepseek/deepseek-v4-flash • medium",
            )
            .render()
        )

        turns = self.pi.parse_transcript(pane)

        assert turns == [
            ("user", "reply with response text here"),
            ("assistant", "response text here"),
            ("user", "ok"),
            ("assistant", "ok"),
        ]

    def _clipped_pane(self) -> str:
        # Top of the captured scrollback is a scrolled-past assistant turn whose
        # opening lines are gone — only its tail is visible.  Forward "block 0 is
        # the user" parity would mislabel every turn; bottom-anchored parity (the
        # last completed block is the assistant reply) keeps them correct.
        return (
            PaneSimulator()
            .add(
                " the answer is 42.",
                "",
                "",
                " what about 7 times 6?",
                "",
                "",
                " that is also 42.",
                "",
                "─" * 80,
                " ~/project",
                "0.0%/66k (auto)  deepseek/deepseek-v4-flash • medium",
            )
            .render()
        )

    def test_clipped_top_assistant_turn_keeps_roles_anchored_to_bottom(self):
        turns = self.pi.parse_transcript(self._clipped_pane())
        assert turns == [
            ("assistant", "the answer is 42."),
            ("user", "what about 7 times 6?"),
            ("assistant", "that is also 42."),
        ]


# ─────────────────────────────────────────────────────────────────────────────
# Antigravity adapter — synthetic pane (prompt-marker parser)
# ─────────────────────────────────────────────────────────────────────────────


class TestAntigravityTranscript:
    agy = AntigravityAdapter()

    def _make_pane(self) -> str:
        return (
            PaneSimulator()
            .add(
                "Antigravity CLI",
                "? for shortcuts",
                "",
                "> reply to me with \"ok\"",
                "",
                "ok",
                "",
                "> reply to me with a short poem",
                "",
                "line one",
                "line two",
                "",
                ">",
                "↑/↓ navigate",
            )
            .render()
        )

    def test_two_completed_turns(self):
        turns = self.agy.parse_transcript(self._make_pane())
        assert len(turns) == 4

    def test_first_user_text(self):
        turns = self.agy.parse_transcript(self._make_pane())
        assert turns[0] == ("user", 'reply to me with "ok"')

    def test_multiline_second_reply_preserved(self):
        turns = self.agy.parse_transcript(self._make_pane())
        assert turns[3] == ("assistant", "line one\nline two")

    def test_antigravity_chrome_dropped(self):
        turns = self.agy.parse_transcript(self._make_pane())
        all_text = " ".join(body for _, body in turns)
        assert "Antigravity CLI" not in all_text
        assert "shortcuts" not in all_text
        assert "navigate" not in all_text


# ─────────────────────────────────────────────────────────────────────────────
# Cursor adapter — synthetic pane (full-width user-line parser)
# ─────────────────────────────────────────────────────────────────────────────


class TestCursorTranscript:
    cu = CursorAdapter()

    def _make_pane(self) -> str:
        # Real Cursor v2026+: tmux strips all trailing whitespace, so full-width
        # padding is gone.  User and assistant messages are both 2-space-indented;
        # turns are separated by 2 blank lines.  The live-input box (→ line) and
        # chrome footer are filtered before paragraph detection.
        return (
            PaneSimulator()
            .add(
                "  Cursor Agent",
                "  v2026.05.20-2b5dd59",
                "  Use /auto-run to skip all approvals.",
                "",
                "",
                "  list all files in this repo",
                "",
                "",
                "  The repo contains: README.md, main.py, tests/",
                "",
                "",
                "  summarize main.py",
                "",
                "",
                "  main.py is an entry point.",
                "",
                "",
                "  → Add a follow-up",
                "  Composer 2.5   Auto-run",
                "  ~/project",
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
        assert "auto-run" not in all_text.lower()

    def test_double_blank_turns_and_auto_usage_footer_parse_cleanly(self):
        # Real Cursor v2026 uses 2 blank lines between turns (not 1).
        pane = (
            PaneSimulator()
            .add(
                "  Reply in one short sentence with the exact words: response text here",
                "",
                "",
                "  response text here",
                "",
                "",
                '  good work. reply "ok"',
                "",
                "",
                "  ok",
                "",
                "  Auto · 7.3%",
                "  ~/Documents/code/testingmurderharness",
            )
            .render()
        )

        turns = self.cu.parse_transcript(pane)

        assert turns == [
            ("user", "Reply in one short sentence with the exact words: response text here"),
            ("assistant", "response text here"),
            ("user", 'good work. reply "ok"'),
            ("assistant", "ok"),
        ]

    def test_banner_tip_without_slash_command_filtered(self):
        # "Use subagents to …" (no /slash prefix) must be treated as chrome,
        # not as content that desynchs turn parity.
        pane = (
            PaneSimulator()
            .add(
                "  Cursor Agent",
                "  v2026.05.24-dda726e",
                "  Use subagents to parallelize work and preserve context.",
                "",
                "",
                "  say hello in one word",
                "",
                "",
                "  Hello",
                "",
                "  → Add a follow-up",
                "  Composer 2.5   Auto-run",
                "  ~/project",
            )
            .render()
        )
        turns = self.cu.parse_transcript(pane)
        assert turns == [("user", "say hello in one word"), ("assistant", "Hello")]


# ─────────────────────────────────────────────────────────────────────────────
# Cursor adapter — real tool-output fixture
# ─────────────────────────────────────────────────────────────────────────────


class TestCursorToolOutputTranscript:
    """Cursor pane with tool file-reading activity (real recording shape).

    The assistant's first turn uses ──── rule separators inside its tool-output
    display (single blank on each side).  The old CursorTranscriptParser split
    these as turn boundaries, so content ended up labeled as user turns.
    """

    cu = CursorAdapter()

    def test_three_turns_with_tool_output(self):
        # clipped assistant (turn 0) + user prompt + in-progress assistant
        turns = self.cu.parse_transcript(CURSOR_TOOL_OUTPUT)
        assert len(turns) == 3

    def test_first_block_is_assistant(self):
        turns = self.cu.parse_transcript(CURSOR_TOOL_OUTPUT)
        assert turns[0][0] == "assistant"

    def test_assistant_tool_output_contains_file_summary(self):
        turns = self.cu.parse_transcript(CURSOR_TOOL_OUTPUT)
        asst_text = " ".join(body for role, body in turns if role == "assistant")
        assert "Repo contents" in asst_text
        assert "Bottom line" in asst_text

    def test_user_prompt_correctly_identified(self):
        turns = self.cu.parse_transcript(CURSOR_TOOL_OUTPUT)
        user_turns = [body for role, body in turns if role == "user"]
        assert len(user_turns) == 1
        assert "Search the entire repo" in user_turns[0]

    def test_truncated_hint_in_assistant_not_user(self):
        # "… truncated (N more lines) · ctrl+o to expand" is tool output;
        # must appear in an assistant turn, not a user turn.
        turns = self.cu.parse_transcript(CURSOR_TOOL_OUTPUT)
        user_text = " ".join(body for role, body in turns if role == "user")
        asst_text = " ".join(body for role, body in turns if role == "assistant")
        assert "truncated" not in user_text
        assert "truncated" in asst_text

    def test_absolute_paths_in_tool_output_preserved(self):
        # Absolute paths emitted by find/shell commands (no "· branch" suffix)
        # must not be eaten by the CWD banner chrome regex.
        turns = self.cu.parse_transcript(CURSOR_TOOL_OUTPUT)
        asst_text = " ".join(body for role, body in turns if role == "assistant")
        assert ".gitignore" in asst_text

    def test_chrome_not_in_any_turn(self):
        turns = self.cu.parse_transcript(CURSOR_TOOL_OUTPUT)
        all_text = " ".join(body for _, body in turns)
        assert "Composer" not in all_text
        assert "Add a follow-up" not in all_text
        assert "ctrl+c to stop" not in all_text


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
