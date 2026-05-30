"""Tests for ``parse_harness_model_list`` and adapter model selection config.

``parse_harness_model_list`` is a pure function tested here against two real
pane fixtures extracted from recordings:

  codex_model_list.txt  — Codex ``/model`` numbered picker
      (tools/testing/recordings/20260523-215413, frame 23)

  pi_model_picker.txt   — Pi ``/model`` interactive picker with provider/model IDs
      (tools/testing/recordings/20260526-080137-pi-model-deepseek-v4-flash, frame 6)

Key assertions:
  - Model IDs are extracted correctly for both harness styles
  - Chrome / UI labels (headers, tips, press-enter hints, status bars) are absent
  - Adapter class vars (model_list_command, available_startup_models) match expectations
"""

from __future__ import annotations

from pathlib import Path

from murder.harnesses.claude_code import ClaudeCodeAdapter
from murder.harnesses.codex import CodexAdapter
from murder.harnesses.cursor import CursorAdapter
from murder.harnesses.parsing import (
    parse_antigravity_model_choices,
    parse_claude_code_model_choices,
    parse_harness_model_list,
    parse_numbered_effort_choices,
    parse_numbered_model_choices,
)
from murder.harnesses.pi_harness import PiAdapter
from tests.support.simulators import PaneSimulator

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "harness_panes"


def _load(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding="utf-8")


def _pane(name: str) -> str:
    """Load fixture and strip ``# source:`` provenance comment lines."""
    return "\n".join(
        line for line in _load(name).splitlines() if not line.startswith("#")
    )


CODEX_MODEL_LIST = _pane("codex_model_list.txt")
PI_MODEL_PICKER = _pane("pi_model_picker.txt")
CC_MODEL_PICKER = """
Select Model
Switch between Claude models. Your pick becomes the default for new sessions.

  1. Default (recommended)  Sonnet 4.6 · Best for everyday tasks
  2. Sonnet (1M context)   Sonnet 4.6 with 1M context
> 3. Opus ✓                Opus 4.8 · Most capable for complex work
  4. Haiku                 Haiku 4.5 · Fastest for quick answers

● High effort (default) ←/→ to adjust
"""
CODEX_REASONING_PICKER = """
Select Reasoning Level for modelnamefoo

1. Low
2. Medium (default)
3. High
4. Extra High
"""


# ─────────────────────────────────────────────────────────────────────────────
# Codex /model picker (numbered list)
# ─────────────────────────────────────────────────────────────────────────────


def test_codex_model_list_extracts_five_models():
    models = parse_harness_model_list(CODEX_MODEL_LIST)
    ids = [m[0] for m in models]
    assert len(ids) == 5


def test_codex_model_list_contains_gpt55():
    models = parse_harness_model_list(CODEX_MODEL_LIST)
    ids = [m[0] for m in models]
    assert "gpt-5.5" in ids


def test_codex_model_list_contains_gpt54():
    models = parse_harness_model_list(CODEX_MODEL_LIST)
    ids = [m[0] for m in models]
    assert "gpt-5.4" in ids


def test_codex_model_list_contains_mini():
    models = parse_harness_model_list(CODEX_MODEL_LIST)
    ids = [m[0] for m in models]
    assert "gpt-5.4-mini" in ids


def test_codex_model_list_contains_codex():
    models = parse_harness_model_list(CODEX_MODEL_LIST)
    ids = [m[0] for m in models]
    assert "gpt-5.3-codex" in ids


def test_codex_model_list_contains_gpt52():
    models = parse_harness_model_list(CODEX_MODEL_LIST)
    ids = [m[0] for m in models]
    assert "gpt-5.2" in ids


def test_codex_model_list_no_chrome_in_ids():
    # UI chrome like "Select Model" / "Press enter" must not appear as model IDs
    models = parse_harness_model_list(CODEX_MODEL_LIST)
    ids = [m[0] for m in models]
    chrome_fragments = ["select", "press", "enter", "access", "esc", "tip"]
    for model_id in ids:
        for frag in chrome_fragments:
            assert frag not in model_id.lower(), f"Chrome fragment {frag!r} in model id {model_id!r}"


def test_codex_model_list_order_preserved():
    # Models must appear in picker order (gpt-5.5 first, gpt-5.2 last)
    models = parse_harness_model_list(CODEX_MODEL_LIST)
    ids = [m[0] for m in models]
    assert ids.index("gpt-5.5") < ids.index("gpt-5.2")


def test_codex_model_list_no_duplicates():
    models = parse_harness_model_list(CODEX_MODEL_LIST)
    ids = [m[0] for m in models]
    assert len(ids) == len(set(ids))


# ─────────────────────────────────────────────────────────────────────────────
# Pi /model interactive picker (provider/model rows)
# ─────────────────────────────────────────────────────────────────────────────


def test_pi_model_picker_extracts_models():
    models = parse_harness_model_list(PI_MODEL_PICKER)
    assert len(models) >= 4, "Expected at least 4 models from Pi picker"


def test_pi_model_picker_contains_deepseek_pro():
    models = parse_harness_model_list(PI_MODEL_PICKER)
    ids = [m[0] for m in models]
    assert "deepseek/deepseek-v4-pro" in ids


def test_pi_model_picker_contains_deepseek_flash():
    models = parse_harness_model_list(PI_MODEL_PICKER)
    ids = [m[0] for m in models]
    assert "deepseek/deepseek-v4-flash" in ids


def test_pi_model_picker_contains_gemini():
    models = parse_harness_model_list(PI_MODEL_PICKER)
    ids = [m[0] for m in models]
    assert "google/gemini-3.1-pro-preview" in ids


def test_pi_model_picker_contains_local_gguf():
    # Local model weights (.gguf files) should be extracted
    models = parse_harness_model_list(PI_MODEL_PICKER)
    ids = [m[0] for m in models]
    assert any(id_.endswith(".gguf") for id_ in ids)


def test_pi_model_picker_no_chrome_in_ids():
    models = parse_harness_model_list(PI_MODEL_PICKER)
    ids = [m[0] for m in models]
    chrome_fragments = ["scope:", "tab scope", "model name:", "ctrl+p", "0.0%"]
    for model_id in ids:
        for frag in chrome_fragments:
            assert frag not in model_id.lower(), f"Chrome {frag!r} in {model_id!r}"


def test_pi_model_picker_no_duplicates():
    models = parse_harness_model_list(PI_MODEL_PICKER)
    ids = [m[0] for m in models]
    assert len(ids) == len(set(ids))


def test_pi_model_picker_status_bar_not_a_model():
    # "0.0%/1.0M (auto)" status line must not produce a model entry
    models = parse_harness_model_list(PI_MODEL_PICKER)
    ids = [m[0] for m in models]
    assert not any("%" in id_ for id_ in ids)


# ─────────────────────────────────────────────────────────────────────────────
# Adapter-specific numbered pickers
# ─────────────────────────────────────────────────────────────────────────────


def test_codex_numbered_model_choices_preserve_indices():
    choices = parse_numbered_model_choices(CODEX_MODEL_LIST)
    by_id = {choice.model_id: choice for choice in choices}
    assert by_id["gpt-5.4-mini"].index == 3
    assert by_id["gpt-5.4"].current is True


def test_codex_reasoning_choices_parse_extra_high():
    choices = parse_numbered_effort_choices(CODEX_REASONING_PICKER)
    by_effort = {choice.effort: choice for choice in choices}
    assert by_effort["medium"].index == 2
    assert by_effort["xhigh"].index == 4


def test_cc_model_picker_extracts_runtime_model_ids():
    choices = parse_claude_code_model_choices(CC_MODEL_PICKER)
    ids = [choice.model_id for choice in choices]
    assert ids == ["sonnet", "opus", "haiku"]


def test_cc_model_picker_marks_current_model():
    choices = parse_claude_code_model_choices(CC_MODEL_PICKER)
    current = [choice.model_id for choice in choices if choice.current]
    assert current == ["opus"]


# ─────────────────────────────────────────────────────────────────────────────
# Adapter discovery config
# ─────────────────────────────────────────────────────────────────────────────


def test_cc_model_list_command_is_slash_model():
    assert ClaudeCodeAdapter.model_list_command == "/model"


def test_cursor_model_list_command_is_none():
    # Cursor's picker has display names without usable IDs; discovery is off
    assert CursorAdapter.model_list_command is None


def test_codex_model_list_command_is_slash_model():
    assert CodexAdapter.model_list_command == "/model"


def test_pi_model_list_command_is_slash_model():
    assert PiAdapter.model_list_command == "/model"


def test_antigravity_model_list_command_is_slash_model():
    from murder.harnesses.antigravity import AntigravityAdapter

    assert AntigravityAdapter.model_list_command == "/model"


def test_antigravity_picker_slugs_gemini_pro_low():
    choices = parse_antigravity_model_choices(_pane("agy_model_picker.txt"))
    by_id = {choice.model_id: choice for choice in choices}
    assert "gemini-3-1-pro" in by_id
    assert "gemini-3-5-flash" in by_id


# ─────────────────────────────────────────────────────────────────────────────
# parse_harness_model_list — synthetic edge cases
# ─────────────────────────────────────────────────────────────────────────────


def test_empty_pane_returns_empty_list():
    assert parse_harness_model_list("") == []


def test_chrome_only_pane_returns_empty_list():
    pane = PaneSimulator().add(
        "Select Model and Effort",
        "Press enter to confirm or esc to go back",
        "─────────────────",
    ).render()
    assert parse_harness_model_list(pane) == []


def test_single_model_with_code_hint():
    pane = "  Use `gpt-5.5` for complex tasks"
    models = parse_harness_model_list(pane)
    ids = [m[0] for m in models]
    assert "gpt-5.5" in ids


def test_version_banner_is_not_a_model():
    # "v0.133.0" style banners must not be returned as model IDs
    pane = "OpenAI Codex (v0.133.0)"
    models = parse_harness_model_list(pane)
    ids = [m[0] for m in models]
    assert not any("v0." in id_ for id_ in ids)


def test_cwd_path_is_not_a_model():
    pane = "~/Documents/code/murder"
    assert parse_harness_model_list(pane) == []


def test_provider_model_slug_extracted():
    pane = "→ anthropic/claude-sonnet-4-6 [active]"
    models = parse_harness_model_list(pane)
    ids = [m[0] for m in models]
    assert "anthropic/claude-sonnet-4-6" in ids
