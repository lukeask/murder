"""Parsers checked against real harness pane captures.

Fixtures under ``tests/fixtures/harness_panes/`` were captured from live
``claude`` (v2.1.139), ``codex`` (v0.130.0), ``agent``/cursor
(v2026.05.09), and ``pi`` (v0.71.0) sessions in a scratch repo on 2026-05-12.
They're lightly trimmed for length but otherwise verbatim — re-capture and
update them when a harness CLI changes its UI.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from murder.harnesses import get as get_harness
from murder.harnesses.parsing import parse_harness_model_list
from murder.harnesses.usage import parse_claude_usage_pane, parse_codex_status_pane

_PANES = Path(__file__).resolve().parents[1] / "fixtures" / "harness_panes"
_CURSOR_PANES = Path(__file__).resolve().parents[1] / "fixtures" / "cursor_panes"


def _pane(name: str) -> str:
    return (_PANES / f"{name}.txt").read_text()


def _cursor_pane(name: str) -> str:
    return (_CURSOR_PANES / f"{name}.txt").read_text()


# ── /model discovery ───────────────────────────────────────────────────────


def test_codex_model_picker_lists_real_models() -> None:
    ids = [model_id for model_id, _ in parse_harness_model_list(_pane("codex_model_picker"))]
    assert ids == ["gpt-5.5", "gpt-5.4", "gpt-5.4-mini", "gpt-5.3-codex", "gpt-5.2"]


def test_codex_model_picker_skips_tip_urls_but_keeps_descriptions() -> None:
    pane = """
  Tip: Join the OpenAI community Discord: https://discord.gg/openai

  Select Model and Effort

› 1. gpt-5.5 (current)  Frontier model for complex coding, research, and real-world work.
  2. gpt-5.4            Strong model for everyday coding.
"""
    assert parse_harness_model_list(pane) == [
        ("gpt-5.5", "Gpt 5.5"),
        ("gpt-5.4", "gpt-5.4 Strong model for everyday coding."),
    ]


def test_codex_idle_footer_is_not_a_model_list() -> None:
    pane = """
╭──────────────────────────────────────────────╮
│ >_ OpenAI Codex (v0.130.0)                   │
│                                              │
│ model:     gpt-5.5 high   /model to change   │
│ directory: ~/Agents/projects/graphvisexplore │
╰──────────────────────────────────────────────╯

  Tip: NEW: Prevent sleep while running is now available in /experimental.


› Implement {feature}

  gpt-5.5 medium · ~/Agents/projects/graphvisexplore
"""
    assert parse_harness_model_list(pane) == []


def test_pi_model_picker_lists_provider_slugs_and_local_weights() -> None:
    ids = [model_id for model_id, _ in parse_harness_model_list(_pane("pi_model_picker"))]
    assert ids == [
        "deepseek/deepseek-v4-pro",
        "deepseek/deepseek-v4-flash",
        "google/gemini-3.1-pro-preview",
        "moonshotai/kimi-k2.6",
        "qwen/qwen3.6-plus",
        "qwen/qwen3.6-35b-a3b",
        "minimax/minimax-m2.7",
        "xiaomi/mimo-v2.5-pro",
        "Qwen3.6-35B-A3B-Q8_0.gguf",
    ]


def test_claude_model_picker_has_no_machine_ids_so_yields_nothing() -> None:
    # CC's `/model` dialog is human labels ("Default", "Opus ✔", "Haiku") with
    # no `--model` ids — the parser must not invent garbage from it (and the
    # adapter sets model_list_command=None so discovery is skipped entirely).
    assert parse_harness_model_list(_pane("claude_model_picker")) == []
    assert get_harness("claude_code").model_list_command is None
    assert get_harness("cursor").model_list_command is None
    assert get_harness("codex").model_list_command == "/model"
    assert get_harness("pi").model_list_command == "/model"


def test_filesystem_path_lines_are_not_parsed_as_models() -> None:
    # The codex banner / pi status bar carry a `~/path/to/cwd` line; a path
    # segment must never surface as a `provider/model` choice.
    for pane_name in ("codex_model_picker", "pi_model_picker", "claude_model_picker"):
        ids = [model_id for model_id, _ in parse_harness_model_list(_pane(pane_name))]
        assert not any("/" in mid and mid[0].isupper() for mid in ids)
        assert "Agents/projects" not in ids


# ── usage / status ─────────────────────────────────────────────────────────


def test_codex_status_pane_handles_used_phrasing() -> None:
    pane = "  5h limit:   [████░] 12% used (resets 09:00)\n  Weekly limit: [██░] 80% used\n"
    now = datetime(2026, 5, 12, 12, 0, tzinfo=ZoneInfo("UTC"))
    by_name = {w.name: w for w in parse_codex_status_pane(pane, now=now).windows}
    assert by_name["5h"].percent_used == 12.0
    assert by_name["weekly"].percent_used == 80.0
    assert by_name["weekly"].reset_at is None


def test_codex_status_fixture_left_suffix_is_remaining_quota() -> None:
    """v0.130 /status prints `N% left`, i.e. quota remaining — percent_used is 100−N."""
    now = datetime(2026, 5, 15, 12, 0, tzinfo=ZoneInfo("UTC"))
    by_name = {w.name: w for w in parse_codex_status_pane(_pane("codex_status"), now=now).windows}
    assert by_name["5h"].percent_used == 3.0
    assert by_name["weekly"].percent_used == 6.0


def test_claude_usage_pane_still_parses() -> None:
    now = datetime(2026, 5, 12, 18, 0, tzinfo=ZoneInfo("America/New_York"))
    status = parse_claude_usage_pane(_pane("claude_usage"), now=now, fetched_at="t")
    assert status.harness == "claude_code"
    assert [w.name for w in status.windows] == ["current_session"]
    assert status.windows[0].percent_used == 20.0
    assert status.windows[0].reset_at == "2026-05-12T18:30:00-04:00"
    assert status.session is not None and status.session.cost_usd == 0.0


# ── transcript parsing ─────────────────────────────────────────────────────


def test_claude_transcript_extracts_user_and_assistant_turns() -> None:
    turns = get_harness("claude_code").parse_transcript(_pane("claude_transcript"))
    assert turns == [
        ("user", "scaffold a graph editor prototype"),
        (
            "assistant",
            "I'll set up the shell now.\n\nWrite(index.html)\n"
            "Wrote 42 lines to index.html\n\n"
            "Done — node palette is on the left, drag onto the canvas.",
        ),
    ]
    # The `❯ /model` echo, the `✻ Churned for 1s` spinner, the `────` rules,
    # the trailing empty `❯`, and the `bypass permissions` status bar are gone.
    flat = "\n".join(t for _, t in turns)
    assert "/model" not in flat
    assert "Churned" not in flat
    assert "bypass permissions" not in flat
    assert "────" not in flat


def test_codex_transcript_extracts_turns_and_drops_input_box() -> None:
    turns = get_harness("codex").parse_transcript(_pane("codex_transcript"))
    assert turns[0] == ("user", "scaffold a graph editor prototype")
    assert turns[1][0] == "assistant"
    assert "node palette" in turns[1][1]
    # The footer placeholder ("Find and fix a bug in @filename") + its status
    # bar must not become a trailing turn.
    assert all("Find and fix a bug" not in body for _, body in turns)
    assert not any(role == "user" and "filename" in body for role, body in turns)
    assert len(turns) == 2


def test_pi_transcript_extracts_user_and_assistant_turns() -> None:
    turns = get_harness("pi").parse_transcript(_pane("pi_transcript"))
    assert turns == [
        ("user", "summarize this repo in one sentence"),
        (
            "assistant",
            "Murder is a local-first harness for coordinating multiple coding agents through\n"
            "tmux, SQLite-backed state, and a Textual TUI.",
        ),
        ("user", "list two parser risks"),
        (
            "assistant",
            "Two risks:\n"
            "- confusing live input/status chrome with conversation turns\n"
            "- treating model-rendered shell prompts as user prompts",
        ),
    ]
    flat = "\n".join(t for _, t in turns)
    assert "deepseek-v4-flash" not in flat
    assert "0.0%/" not in flat
    assert "ctrl+c/ctrl+d" not in flat


def test_pi_model_picker_is_not_parsed_as_transcript() -> None:
    assert get_harness("pi").parse_transcript(_pane("pi_transcript_model_picker_not_chat")) == []


def test_pi_extract_last_message_drops_footer() -> None:
    pane = _pane("pi_transcript")
    assert get_harness("pi").extract_last_message(pane) == (
        "Two risks:\n"
        "- confusing live input/status chrome with conversation turns\n"
        "- treating model-rendered shell prompts as user prompts"
    )


def test_pi_protocol_lines_remain_model_text_not_footer_noise() -> None:
    pane = """
> verify the ticket

I checked the implementation.
>>> CHECK: tests pass
>>> DONE

/home/user/Documents/code/murder (main)
0.0%/1.0M (auto)                             (deepseek) deepseek-v4-flash • high
"""
    adapter = get_harness("pi")
    assert adapter.parse_transcript(pane) == [
        ("user", "verify the ticket"),
        ("assistant", "I checked the implementation.\n>>> CHECK: tests pass\n>>> DONE"),
    ]
    assert adapter.detect_checks(pane) == ["tests pass"]
    assert adapter.detect_done(pane)


def test_cursor_transcript_extracts_padded_user_prompt_and_assistant_reply() -> None:
    turns = get_harness("cursor").parse_transcript(_cursor_pane("idle_after_first_turn"))
    assert turns[0] == (
        "user",
        "List the files in this directory and tell me what you see. Take your time.",
    )
    assert turns[1][0] == "assistant"
    assert "Listing the workspace directory" in turns[1][1]
    assert "Bottom line:" in turns[1][1]
    flat = "\n".join(text for _, text in turns)
    assert "Cursor Agent" not in flat
    assert "Add a follow-up" not in flat
    assert "Composer 2" not in flat
    assert "/tmp/murder-smoke · master" not in flat
    assert len(turns) == 2


def test_cursor_busy_transcript_keeps_turn_boundaries_and_drops_status_chrome() -> None:
    turns = get_harness("cursor").parse_transcript(_cursor_pane("busy_running_tool"))
    assert [role for role, _ in turns] == [
        "user",
        "assistant",
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert turns[2] == ("user", 'Run "ls -la" via the shell tool and tell me what\'s there.')
    assert turns[4] == ("user", 'Sleep for 10 seconds via the shell tool, then say "done".')
    assert "$ ls -la /tmp/murder-smoke 702ms" in turns[3][1]
    assert "$ sleep 10 6.4s" in turns[5][1]
    flat = "\n".join(text for _, text in turns)
    assert "ctrl+c to stop" not in flat
    assert "Running  21 tokens" not in flat


def test_cursor_composing_prompt_without_model_text_is_not_a_turn() -> None:
    assert get_harness("cursor").parse_transcript(_cursor_pane("busy_composing")) == []


def test_cursor_model_picker_is_not_parsed_as_transcript() -> None:
    assert get_harness("cursor").parse_transcript(_pane("cursor_model_picker")) == []


def test_cursor_extract_last_message_drops_footer() -> None:
    message = get_harness("cursor").extract_last_message(_cursor_pane("idle_after_first_turn"))
    assert message is not None
    assert message.endswith("template checkout named “murder-smoke.”")
    assert "Composer 2" not in message


def test_cursor_protocol_lines_remain_model_text_not_footer_noise() -> None:
    pane = """
  Cursor Agent
  v2026.04.30-4edb302

                                                                               
  verify the ticket                                                            
                                                                               

  I checked the implementation.
  >>> CHECK: tests pass
  >>> DONE

 ▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄
  → Add a follow-up                                                            
 ▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀
  Composer 2 · 5.9%                                                   Auto-run
  /tmp/murder-smoke · master
"""
    adapter = get_harness("cursor")
    assert adapter.parse_transcript(pane) == [
        ("user", "verify the ticket"),
        ("assistant", "I checked the implementation.\n>>> CHECK: tests pass\n>>> DONE"),
    ]
    assert adapter.detect_checks(pane) == ["tests pass"]
    assert adapter.detect_done(pane)


def test_cursor_transcript_parser_is_idempotent_on_reparse() -> None:
    adapter = get_harness("cursor")
    pane = _cursor_pane("busy_running_tool")
    assert adapter.parse_transcript(pane) == adapter.parse_transcript(pane)


@pytest.mark.parametrize(
    ("kind", "fixture"),
    [("claude_code", "claude"), ("codex", "codex"), ("pi", "pi")],
)
def test_transcript_parser_is_idempotent_on_reparse(kind: str, fixture: str) -> None:
    adapter = get_harness(kind)
    pane = _pane(f"{fixture}_transcript")
    assert adapter.parse_transcript(pane) == adapter.parse_transcript(pane)
