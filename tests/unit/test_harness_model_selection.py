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

from murder.llm.harnesses.claude_code import ClaudeCodeAdapter, _claude_model_id
from murder.llm.harnesses.codex import CodexAdapter
from murder.llm.harnesses.cursor import CursorAdapter
from murder.llm.harnesses.cursor import _cursor_model_id_from_label
from murder.llm.harnesses.parsing import (
    parse_antigravity_model_choices,
    parse_claude_code_model_choices,
    parse_cursor_model_list,
    parse_cursor_model_page,
    parse_harness_model_list,
    parse_numbered_effort_choices,
    parse_numbered_model_choices,
)
from murder.llm.harnesses.pi_harness import PiAdapter
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
# Real Claude Code v2.1.172 `/model` radio dialog (captured live 2026-06-10 via
# tmux). Six numbered rows: five presented models plus a "Custom model" echo of
# the `--model sonnet` override (row 6), which is not a selectable menu model.
CC_MODEL_PICKER = """
  Select model
  Switch between Claude models. Your pick becomes the default for new sessions. For
  other/previous model names, specify with --model.

    1. Default (recommended)  Sonnet 4.6 · Efficient for routine tasks
    2. Sonnet (1M context)    Sonnet 4.6 with 1M context · Draws from usage credits ·
                              $3/$15 per Mtok
    3. Fable                  Fable 5 · Most capable for your hardest and longest-running
                              tasks · Uses your limits ~2× faster than Opus
    4. Opus                   Opus 4.8 · Best for everyday, complex tasks · ~2× usage vs
                              Sonnet
    5. Haiku                  Haiku 4.5 · Fastest for quick answers
  ❯ 6. sonnet ✔               Custom model

  ◐ Medium effort ←/→ to adjust

  Use /fast to turn on Fast mode (Opus 4.8).

  Enter to set as default · s to use this session only · Esc to cancel
"""
# Synthetic Opus Plan Mode variant — Claude Code versions that present an
# "Opus Plan Mode" row must derive the `opusplan` slash id distinct from `opus`.
CC_MODEL_PICKER_WITH_PLAN = """
  Select model

    1. Default (recommended)  Sonnet 4.6 · Efficient for routine tasks
    2. Opus                   Opus 4.8 · Best for everyday, complex tasks
  ❯ 3. Opus Plan Mode ✔       Opus 4.8 · Plans with Opus, executes with Sonnet
    4. Haiku                  Haiku 4.5 · Fastest for quick answers
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


def test_pi_model_picker_drops_extended_keys_control_row():
    # Regression (dogfood bug #8): pi's picker leaked a non-model first row
    # `Set  G Extended Keys On` — a terminal "enable extended keys" control
    # string echoed as text, not a model. It must be filtered from the list.
    from murder.llm.harnesses.transcripts.grammar.pi import _is_pi_chrome

    pane = (
        "Set  G Extended Keys On\n"
        "> \n"
        "  deepseek/deepseek-v4-pro [or]\n"
        "  deepseek/deepseek-v4-flash [or]\n"
    )
    ids = [m[0] for m in parse_harness_model_list(pane)]
    assert "deepseek/deepseek-v4-pro" in ids
    assert "deepseek/deepseek-v4-flash" in ids
    assert not any("extended" in id_.lower() for id_ in ids)
    # The transcript chrome predicate (raw-pane mirror fallback) must also drop
    # it so it never surfaces as a chat line.
    assert _is_pi_chrome("Set  G Extended Keys On")
    assert _is_pi_chrome("  Set  G  Extended Keys Off")
    assert not _is_pi_chrome("deepseek/deepseek-v4-flash [or]")


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


def test_cc_model_picker_extracts_all_presented_models():
    # Every presented row must survive with its correct `/model <id>` slash id;
    # the "Custom model" echo row (6) is dropped (not a selectable menu model).
    choices = parse_claude_code_model_choices(CC_MODEL_PICKER)
    ids = [choice.model_id for choice in choices]
    assert ids == ["default", "sonnet[1m]", "fable", "opus", "haiku"]


def test_cc_model_picker_keeps_sonnet_variants_distinct():
    # `Default` and `Sonnet (1M context)` are the same family but distinct ids;
    # the coarse-family dedupe of the old parser collapsed them.
    choices = parse_claude_code_model_choices(CC_MODEL_PICKER)
    ids = [choice.model_id for choice in choices]
    assert "default" in ids
    assert "sonnet[1m]" in ids
    assert len(ids) == len(set(ids))


def test_cc_model_picker_preserves_row_indices_and_labels():
    by_id = {c.model_id: c for c in parse_claude_code_model_choices(CC_MODEL_PICKER)}
    assert by_id["default"].index == 1
    assert by_id["sonnet[1m]"].index == 2
    assert by_id["fable"].index == 3
    assert by_id["opus"].index == 4
    assert by_id["haiku"].index == 5
    assert by_id["fable"].label.startswith("Fable")


def test_cc_model_picker_drops_custom_model_echo_row():
    choices = parse_claude_code_model_choices(CC_MODEL_PICKER)
    # The custom `--model sonnet` echo (row 6, "Custom model") must not appear as
    # a plain `sonnet` entry alongside the menu models.
    assert all(c.label.lower() != "sonnet" for c in choices)


def test_cc_model_picker_opus_plan_distinct_from_opus():
    choices = parse_claude_code_model_choices(CC_MODEL_PICKER_WITH_PLAN)
    ids = [c.model_id for c in choices]
    assert ids == ["default", "opus", "opusplan", "haiku"]
    current = [c.model_id for c in choices if c.current]
    assert current == ["opusplan"]


# ─────────────────────────────────────────────────────────────────────────────
# Adapter discovery config
# ─────────────────────────────────────────────────────────────────────────────


def test_cc_model_list_command_is_slash_model():
    assert ClaudeCodeAdapter.model_list_command == "/model"


def test_cc_available_startup_models_cover_full_real_set():
    ids = [m[0] for m in ClaudeCodeAdapter.available_startup_models]
    assert ids == ["default", "sonnet[1m]", "fable", "opus", "haiku"]


def test_cc_claude_model_id_passes_through_live_slash_ids():
    # Ids produced by live discovery round-trip back to the exact slash arg.
    assert _claude_model_id("default") == "default"
    assert _claude_model_id("sonnet[1m]") == "sonnet[1m]"
    assert _claude_model_id("opusplan") == "opusplan"
    assert _claude_model_id("fable") == "fable"
    assert _claude_model_id("opus") == "opus"
    assert _claude_model_id("haiku") == "haiku"


def test_cc_claude_model_id_derives_from_human_labels():
    assert _claude_model_id("Sonnet (1M context)") == "sonnet[1m]"
    assert _claude_model_id("Opus Plan Mode") == "opusplan"
    assert _claude_model_id("Default (recommended)") == "default"
    assert _claude_model_id("Opus 4.8") == "opus"
    assert _claude_model_id("Haiku 4.5") == "haiku"
    assert _claude_model_id("Fable 5") == "fable"


def test_cc_claude_model_id_rejects_unknown_and_empty():
    assert _claude_model_id(None) is None
    assert _claude_model_id("") is None
    assert _claude_model_id("not-a-model") is None


def test_cursor_model_list_command_is_slash_model():
    assert CursorAdapter.model_list_command == "/model"


def test_cursor_model_list_parses_first_page_fixture():
    pane = _pane("cursor_model_list.txt")
    assert parse_cursor_model_page(pane) == (1, 10, 27)
    rows = parse_cursor_model_list(pane, _cursor_model_id_from_label)
    assert len(rows) == 10
    ids = {model_id for model_id, _ in rows}
    assert "composer-2.5" in ids
    assert "codex-5-3" in ids
    assert "sonnet-4-6" in ids


def test_codex_model_list_command_is_slash_model():
    assert CodexAdapter.model_list_command == "/model"


def test_pi_model_list_command_is_slash_model():
    assert PiAdapter.model_list_command == "/model"


def test_antigravity_model_list_command_is_slash_model():
    from murder.llm.harnesses.antigravity import AntigravityAdapter

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


# ─────────────────────────────────────────────────────────────────────────────
# 2026-06-06 recordings: CC effort picker, Codex reasoning, Cursor fast mode
# ─────────────────────────────────────────────────────────────────────────────


def test_cc_model_picker_effort_high_parses():
    """Existing CC regex matches ● High effort; this is the passing baseline."""
    adapter = ClaudeCodeAdapter()
    result = adapter.parse_active_model_state(_pane("cc_model_effort_high.txt"))
    assert result is not None
    assert result.effort == "high"


def test_cc_model_picker_effort_low_parses():
    """CC ○ Low effort indicator must be parsed (○ not in current [●•] class)."""
    adapter = ClaudeCodeAdapter()
    result = adapter.parse_active_model_state(_pane("cc_model_effort_low.txt"))
    assert result is not None
    assert result.effort == "low"


def test_cc_model_picker_effort_medium_parses():
    """CC ◐ Medium effort indicator must be parsed (◐ not in current [●•] class)."""
    adapter = ClaudeCodeAdapter()
    result = adapter.parse_active_model_state(_pane("cc_model_effort_medium.txt"))
    assert result is not None
    assert result.effort == "medium"


def test_cc_model_picker_effort_max_parses():
    """CC ◈ Max effort indicator must be parsed (◈ not in current [●•] class)."""
    adapter = ClaudeCodeAdapter()
    result = adapter.parse_active_model_state(_pane("cc_model_effort_max.txt"))
    assert result is not None
    assert result.effort == "max"


def test_cc_fable_banner_reports_model_and_effort():
    """Fable banner ("Fable 5 with medium effort · Claude Pro") must parse.

    Regression: the banner regexes only knew Opus/Sonnet/Haiku, so after
    `/model fable` the post-switch verification in set_model() saw no model,
    returned False, and the spawn was failed even though the switch succeeded.
    """
    adapter = ClaudeCodeAdapter()
    result = adapter.parse_active_model_state(_pane("cc_fable_banner.txt"))
    assert result is not None
    assert result.model == "fable"
    assert result.effort == "medium"


def test_cc_fable_banner_without_effort_text_reports_model():
    """Banner-only fallback must match Fable's single-digit version ("Fable 5 ·")."""
    adapter = ClaudeCodeAdapter()
    result = adapter.parse_active_model_state("▝▜█▛▘  Fable 5 · Claude Pro")
    assert result is not None
    assert result.model == "fable"


def test_cc_fable_promo_prose_is_not_a_model():
    """The "Fable 5 is here!" promo line must not be misread as the active model."""
    adapter = ClaudeCodeAdapter()
    pane = " ▎ Fable 5 is here! Our newest model for complex, long-running work"
    assert adapter.parse_active_model_state(pane) is None


def test_cc_advisor_active_idle_reports_model_and_effort():
    """Advisor-active CC pane (status bar changed) still reports model + effort."""
    adapter = ClaudeCodeAdapter()
    result = adapter.parse_active_model_state(_pane("cc_advisor_active_idle.txt"))
    assert result is not None
    assert result.model is not None
    assert result.effort is not None


def test_cc_advisor_active_idle_is_idle():
    """Advisor-active status bar does not falsely trigger busy detection."""
    adapter = ClaudeCodeAdapter()
    assert adapter.is_idle(_pane("cc_advisor_active_idle.txt")) is True


# ─── Codex reasoning level picker (real recording fixtures) ──────────────────


def test_codex_reasoning_picker_low_from_fixture():
    choices = parse_numbered_effort_choices(_pane("codex_reasoning_low.txt"))
    by_effort = {c.effort: c for c in choices}
    assert by_effort["low"].index == 1
    assert by_effort["medium"].index == 2
    assert by_effort["high"].index == 3
    assert by_effort["xhigh"].index == 4


def test_codex_reasoning_picker_medium_from_fixture():
    choices = parse_numbered_effort_choices(_pane("codex_reasoning_medium.txt"))
    by_effort = {c.effort: c for c in choices}
    assert by_effort["medium"].index == 2


def test_codex_reasoning_picker_high_from_fixture():
    choices = parse_numbered_effort_choices(_pane("codex_reasoning_high.txt"))
    by_effort = {c.effort: c for c in choices}
    assert by_effort["high"].index == 3


def test_codex_reasoning_picker_extrahi_from_fixture():
    choices = parse_numbered_effort_choices(_pane("codex_reasoning_extrahi.txt"))
    by_effort = {c.effort: c for c in choices}
    assert by_effort["xhigh"].index == 4


def test_codex_model_picker_gpt55_fixture_extracts_three_models():
    choices = parse_numbered_model_choices(_pane("codex_model_picker_gpt55.txt"))
    ids = [c.model_id for c in choices]
    assert "gpt-5.5" in ids
    assert "gpt-5.4" in ids
    assert "gpt-5.4-mini" in ids


def test_codex_model_picker_gpt55_marks_current_model():
    choices = parse_numbered_model_choices(_pane("codex_model_picker_gpt55.txt"))
    current = [c.model_id for c in choices if c.current]
    assert current == ["gpt-5.4-mini"]


def test_codex_usage_limit_pane_is_idle():
    """Usage-limit banner does not make the adapter consider the pane busy."""
    adapter = CodexAdapter()
    assert adapter.is_idle(_pane("codex_usage_limit.txt")) is True


# ─── Cursor fast mode (real recording fixtures) ───────────────────────────────


def test_cursor_composer_fast_off_reports_slow():
    """Edit-parameters panel with [ ] Fast → speed is slow."""
    adapter = CursorAdapter()
    result = adapter.parse_active_model_state(_pane("cursor_composer_fast_off.txt"))
    assert result is not None
    assert result.effort == "slow"


def test_cursor_composer_fast_on_reports_fast():
    """Edit-parameters panel with [x] Fast → speed is fast."""
    adapter = CursorAdapter()
    result = adapter.parse_active_model_state(_pane("cursor_composer_fast_on.txt"))
    assert result is not None
    assert result.effort == "fast"


def test_cursor_status_bar_fast_active_reports_fast():
    """Status bar ``Composer 2.5 Fast · 9.1%`` → speed is fast."""
    adapter = CursorAdapter()
    result = adapter.parse_active_model_state(_pane("cursor_status_fast_active.txt"))
    assert result is not None
    assert result.effort == "fast"


def test_cursor_model_list_fast_active_reports_fast():
    """Model list ``Composer 2.5  Fast (Tab to modify)`` → speed is fast."""
    adapter = CursorAdapter()
    result = adapter.parse_active_model_state(_pane("cursor_model_list_fast_active.txt"))
    assert result is not None
    assert result.effort == "fast"


def test_cursor_model_list_with_efforts_extracts_opus_48():
    rows = parse_cursor_model_list(_pane("cursor_model_list_with_efforts.txt"), _cursor_model_id_from_label)
    ids = [r[0] for r in rows]
    assert "opus-4-8" in ids


def test_cursor_model_list_with_efforts_shows_27_total():
    page_num, on_page, total = parse_cursor_model_page(_pane("cursor_model_list_with_efforts.txt"))
    assert total == 27
