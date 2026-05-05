from __future__ import annotations

from murder.config_flow import (
    _existing_models_for_harness,
    _model_rows_for_harness,
    _parse_model_tokens,
    _parse_single_model_token,
    _toggle_harnesses,
)
from murder.harnesses.cursor import CursorAdapter


def test_toggle_harnesses_starts_from_current_selection() -> None:
    assert _toggle_harnesses(["cursor"], [2, 3]) == ["cursor", "claude_code", "codex"]
    assert _toggle_harnesses(["cursor", "codex"], [1]) == ["codex"]


def test_parse_model_tokens_accepts_list_numbers_and_custom_ids() -> None:
    models = [("composer", "Composer"), ("gpt-5.5", "GPT-5.5")]
    assert _parse_model_tokens("1, 2 custom/model", models) == [
        "composer",
        "gpt-5.5",
        "custom/model",
    ]


def test_parse_model_tokens_rejects_out_of_range_numbers() -> None:
    assert _parse_model_tokens("3", [("composer", "Composer")]) == []


def test_parse_single_model_token_accepts_one_model_only() -> None:
    models = [("composer", "Composer"), ("gpt-5.5", "GPT-5.5")]
    assert _parse_single_model_token("2", models) == "gpt-5.5"
    assert _parse_single_model_token("1 2", models) == ""
    assert _parse_single_model_token("", models) is None


def test_cursor_models_are_pulled_from_cursor_harness() -> None:
    assert _model_rows_for_harness("cursor") == CursorAdapter.available_startup_models


def test_existing_single_model_only_prefills_primary_harness() -> None:
    monkey = {"harness": "cursor", "startup_model": "composer"}
    assert _existing_models_for_harness(monkey, "cursor", "cursor") == ["composer"]
    assert _existing_models_for_harness(monkey, "codex", "cursor") == []
