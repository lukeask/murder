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


def _pane(name: str) -> str:
    return (_PANES / f"{name}.txt").read_text()


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


def test_pi_and_cursor_have_no_transcript_parser_yet() -> None:
    # Their REPLs render turns as plain text with no stable marker — parse
    # returns nothing and the TUI shows the raw pane mirror instead.
    assert get_harness("pi").transcript_prompt_markers == ()
    assert get_harness("cursor").transcript_prompt_markers == ()
    assert get_harness("pi").parse_transcript("some\npane\ntext") == []


@pytest.mark.parametrize(("kind", "fixture"), [("claude_code", "claude"), ("codex", "codex")])
def test_transcript_parser_is_idempotent_on_reparse(kind: str, fixture: str) -> None:
    adapter = get_harness(kind)
    pane = _pane(f"{fixture}_transcript")
    assert adapter.parse_transcript(pane) == adapter.parse_transcript(pane)
