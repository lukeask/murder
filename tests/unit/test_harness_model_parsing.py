"""Harness `/models` parser fixtures."""

from __future__ import annotations

from murder.harnesses.parsing import parse_harness_model_list


def test_parse_models_from_tableish_output() -> None:
    pane = """
    /models
    Available models
    ✓ gpt-5.5        flagship
      gpt-5.4-mini   fast
      anthropic/claude-sonnet-4-6
    """

    assert parse_harness_model_list(pane) == [
        ("gpt-5.5", "gpt-5.5 flagship"),
        ("gpt-5.4-mini", "gpt-5.4-mini fast"),
        ("anthropic/claude-sonnet-4-6", "Claude Sonnet 4 6"),
    ]


def test_parse_models_from_labels_with_parenthesized_ids() -> None:
    pane = """
    Select a model
    ● Claude Sonnet (sonnet)
      Claude Opus (`opus`)
      Auto (auto)
    """

    assert parse_harness_model_list(pane) == [
        ("sonnet", "Claude Sonnet (sonnet)"),
        ("opus", "Claude Opus (`opus`)"),
        ("auto", "Auto (auto)"),
    ]


def test_parse_models_deduplicates_repeated_rows() -> None:
    pane = """
    > /models
    - openai/gpt-5.4
    - openai/gpt-5.4
    - openai/gpt-5.5
    """

    assert parse_harness_model_list(pane) == [
        ("openai/gpt-5.4", "Gpt 5.4"),
        ("openai/gpt-5.5", "Gpt 5.5"),
    ]
