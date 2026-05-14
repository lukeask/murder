"""Settings-screen model state helpers."""

from __future__ import annotations

from murder.tui.settings_screen import (
    _model_validation_message,
    _ordered_enabled_models,
)


def test_ordered_enabled_models_puts_default_first() -> None:
    states = {
        "small": "enabled",
        "large": "default",
        "off": "disabled",
    }

    assert _ordered_enabled_models(states, ["small", "large", "off"]) == [
        "large",
        "small",
    ]


def test_model_validation_requires_at_least_one_selected() -> None:
    states = {"a": "disabled", "b": "disabled"}

    assert _model_validation_message(states, ["a", "b"]) == (
        "invalid: select at least one model"
    )


def test_model_validation_rejects_multiple_defaults() -> None:
    states = {"a": "default", "b": "default"}

    assert _model_validation_message(states, ["a", "b"]) == (
        "invalid: choose only one default"
    )


def test_model_validation_allows_enabled_without_default() -> None:
    states = {"a": "enabled", "b": "disabled"}

    assert _model_validation_message(states, ["a", "b"]) is None
