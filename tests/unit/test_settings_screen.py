"""Settings-screen model state helpers."""

from __future__ import annotations

from murder.config import HarnessRoleConfig
from murder.tui.settings_screen import (
    _model_validation_message,
    _ordered_enabled_models,
    _resolve_crow_model_state,
)
from murder.user_config import UserHarnessRolePatch


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


# ── _resolve_crow_model_state — shared by project + global scopes ─────────


def test_resolve_none_patch_returns_empty_enabled_set() -> None:
    harnesses, _options, states = _resolve_crow_model_state(None)
    assert harnesses == set()
    # Every option in every harness is disabled when no patch is given.
    for kind_states in states.values():
        assert all(v == "disabled" for v in kind_states.values())


def test_resolve_project_config_picks_single_harness_and_default() -> None:
    cfg = HarnessRoleConfig(harness="claude_code", startup_model="anthropic/claude-opus-4-7")
    harnesses, options, states = _resolve_crow_model_state(cfg)
    assert harnesses == {"claude_code"}
    assert (
        "anthropic/claude-opus-4-7",
        "anthropic/claude-opus-4-7",
    ) in options["claude_code"] or any(
        m == "anthropic/claude-opus-4-7" for m, _ in options["claude_code"]
    )
    assert states["claude_code"]["anthropic/claude-opus-4-7"] == "default"


def test_resolve_project_config_unifies_pool_default_fallback() -> None:
    """Regression: the project- and global-scope copies used to disagree on
    what to do when `startup_model` is set but isn't a member of
    `startup_models`. Project gave no default; global fell back to the
    pool's first entry. The unified rule must pick the first entry."""
    cfg = HarnessRoleConfig(
        harness="codex",
        startup_model="not-in-pool",
        startup_models=["model-a", "model-b"],
    )
    _harnesses, _options, states = _resolve_crow_model_state(cfg)
    assert states["codex"]["model-a"] == "default"
    assert states["codex"]["model-b"] == "enabled"


def test_resolve_user_patch_with_models_by_harness() -> None:
    patch = UserHarnessRolePatch(
        harnesses=["claude_code", "codex"],
        startup_models_by_harness={
            "claude_code": ["anthropic/claude-opus-4-7", "anthropic/claude-sonnet-4-6"],
            "codex": ["openai/gpt-5.5"],
        },
    )
    harnesses, _options, states = _resolve_crow_model_state(patch)
    assert harnesses == {"claude_code", "codex"}
    # First model in each per-harness list becomes the default.
    assert states["claude_code"]["anthropic/claude-opus-4-7"] == "default"
    assert states["claude_code"]["anthropic/claude-sonnet-4-6"] == "enabled"
    assert states["codex"]["openai/gpt-5.5"] == "default"


def test_resolve_includes_patch_models_not_in_registry_defaults() -> None:
    """The UI must show a model the user wrote into config even if the
    harness registry doesn't list it by default — otherwise their saved
    choice silently disappears."""
    cfg = HarnessRoleConfig(harness="codex", startup_model="custom-private-build")
    _harnesses, options, _states = _resolve_crow_model_state(cfg)
    assert "custom-private-build" in {m for m, _ in options["codex"]}


def test_resolve_project_config_and_equivalent_user_patch_agree() -> None:
    """The whole point of the extraction: both scopes go through one
    algorithm and produce the same UI state for the same intent."""
    cfg = HarnessRoleConfig(
        harness="claude_code",
        startup_model="anthropic/claude-opus-4-7",
        startup_models=["anthropic/claude-opus-4-7", "anthropic/claude-sonnet-4-6"],
    )
    patch = UserHarnessRolePatch(
        harness="claude_code",
        startup_model="anthropic/claude-opus-4-7",
        startup_models=["anthropic/claude-opus-4-7", "anthropic/claude-sonnet-4-6"],
    )
    assert _resolve_crow_model_state(cfg) == _resolve_crow_model_state(patch)
