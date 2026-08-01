"""Characterization tests for settings patch/view/live-application.

Covers absent vs null, ``***`` secret preservation, legacy aliases,
persist-before-live ordering, and override clearing (including the live-sync
fix for null clears).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from murder.app.service.settings import (
    ClearCollaboratorHarness,
    ClearCrowHarnesses,
    ClearPlannerHarness,
    EffectiveHarnessView,
    LiveChange,
    SetCollaboratorHarness,
    SetCrowHarnesses,
    SetPlannerHarness,
    SettingsMutation,
    apply_live_changes,
    apply_settings_patch,
    build_settings_payload,
    commit_settings_mutation,
    mask_llm,
)
from murder.app.service.settings.ports import bundled_role_selection
from murder.app.service.settings.view import (
    restore_api_key_sentinels,
    translate_legacy_llm_provider_fields,
)
from murder.config import Config
from murder.user_config import (
    UserConfig,
    UserHarnessRolePatch,
    UserLlmConfig,
    UserLlmProviderAuth,
    UserLlmProviderSettings,
)


@dataclass
class _FakeRepo:
    config: UserConfig
    saved: list[UserConfig] = field(default_factory=list)
    fail: bool = False

    def load(self) -> UserConfig:
        return self.config

    def save(self, config: UserConfig) -> None:
        if self.fail:
            raise OSError("disk full")
        self.saved.append(config)
        self.config = config


@dataclass
class _FakeLive:
    applied: list[tuple[LiveChange, ...]] = field(default_factory=list)
    fail: bool = False

    def apply(self, changes: Any) -> None:
        if self.fail:
            raise RuntimeError("live apply failed")
        self.applied.append(tuple(changes))


def test_absent_harness_keys_are_noop() -> None:
    current = UserConfig(
        collaborator=UserHarnessRolePatch(harness="codex"),
        planner=UserHarnessRolePatch(harness="cursor"),
        default_crow=UserHarnessRolePatch(harness="pi", harnesses=["pi", "codex"]),
    )
    mutation = apply_settings_patch(current, {"theme": "everforest-dark"})
    assert mutation.config.collaborator is not None
    assert mutation.config.collaborator.harness == "codex"
    assert mutation.config.planner is not None
    assert mutation.config.planner.harness == "cursor"
    assert mutation.config.default_crow is not None
    assert mutation.config.default_crow.harnesses == ["pi", "codex"]
    assert mutation.live_changes == ()


def test_null_clears_overrides_and_enqueues_clear_live_changes() -> None:
    current = UserConfig(
        collaborator=UserHarnessRolePatch(harness="codex"),
        planner=UserHarnessRolePatch(harness="cursor"),
        default_crow=UserHarnessRolePatch(harness="pi", harnesses=["pi", "codex"]),
    )
    mutation = apply_settings_patch(
        current,
        {
            "collaborator_harness": None,
            "planner_harness": None,
            "crow_harnesses": None,
        },
    )
    assert mutation.config.collaborator is not None
    assert mutation.config.collaborator.harness is None
    assert mutation.config.planner is not None
    assert mutation.config.planner.harness is None
    assert mutation.config.default_crow is not None
    assert mutation.config.default_crow.harness is None
    assert mutation.config.default_crow.harnesses is None
    assert mutation.live_changes == (
        ClearCollaboratorHarness(),
        ClearPlannerHarness(),
        ClearCrowHarnesses(),
    )


def test_assign_enqueues_set_live_changes() -> None:
    mutation = apply_settings_patch(
        UserConfig(),
        {
            "collaborator_harness": "codex",
            "planner_harness": "cursor",
            "crow_harnesses": ["claude_code", "pi"],
        },
    )
    assert mutation.config.collaborator is not None
    assert mutation.config.collaborator.harness == "codex"
    assert mutation.config.planner is not None
    assert mutation.config.planner.harness == "cursor"
    assert mutation.config.default_crow is not None
    assert mutation.config.default_crow.harness == "claude_code"
    assert mutation.config.default_crow.harnesses == ["claude_code", "pi"]
    assert mutation.live_changes == (
        SetCollaboratorHarness("codex"),
        SetPlannerHarness("cursor"),
        SetCrowHarnesses(harness="claude_code", harnesses=("claude_code", "pi")),
    )


def test_single_crow_harness_clears_pool() -> None:
    mutation = apply_settings_patch(
        UserConfig(default_crow=UserHarnessRolePatch(harnesses=["pi", "codex"])),
        {"crow_harnesses": ["cursor"]},
    )
    assert mutation.config.default_crow is not None
    assert mutation.config.default_crow.harness == "cursor"
    assert mutation.config.default_crow.harnesses is None
    assert mutation.live_changes == (
        SetCrowHarnesses(harness="cursor", harnesses=None),
    )


def test_api_key_sentinel_preserves_stored_secret() -> None:
    current = UserConfig(
        llm=UserLlmConfig(
            providers={
                "groq": UserLlmProviderSettings(
                    type="groq",
                    auth=UserLlmProviderAuth(api_key="secret-key"),
                )
            }
        )
    )
    mutation = apply_settings_patch(
        current,
        {
            "llm": {
                "providers": {
                    "groq": {"auth": {"api_key": "***"}, "enabled": False},
                }
            }
        },
    )
    assert mutation.config.llm is not None
    assert mutation.config.llm.providers["groq"].auth.api_key == "secret-key"
    assert mutation.config.llm.providers["groq"].enabled is False


def test_empty_api_key_clears_secret() -> None:
    current = UserConfig(
        llm=UserLlmConfig(
            providers={
                "groq": UserLlmProviderSettings(
                    type="groq",
                    auth=UserLlmProviderAuth(api_key="secret-key"),
                )
            }
        )
    )
    mutation = apply_settings_patch(
        current,
        {"llm": {"providers": {"groq": {"auth": {"api_key": ""}}}}},
    )
    assert mutation.config.llm is not None
    assert mutation.config.llm.providers["groq"].auth.api_key == ""


def test_legacy_api_key_and_base_url_aliases_translate_on_patch() -> None:
    mutation = apply_settings_patch(
        UserConfig(),
        {
            "llm": {
                "providers": {
                    "custom": {
                        "type": "openai_compatible",
                        "name": "Custom",
                        "api_key": "legacy-secret",
                        "base_url": "https://example.test/v1",
                    }
                }
            }
        },
    )
    assert mutation.config.llm is not None
    provider = mutation.config.llm.providers["custom"]
    assert provider.auth.api_key == "legacy-secret"
    assert provider.endpoint == "https://example.test/v1"


def test_mask_llm_redacts_and_projects_legacy_aliases() -> None:
    llm = UserLlmConfig(
        providers={
            "groq": UserLlmProviderSettings(
                type="groq",
                endpoint="https://api.groq.com",
                auth=UserLlmProviderAuth(api_key="secret-key"),
            )
        }
    )
    masked = mask_llm(llm)
    provider = masked["providers"]["groq"]
    assert provider["auth"]["api_key"] == "***"
    assert provider["api_key"] == "***"
    assert provider["base_url"] == "https://api.groq.com"
    assert provider["endpoint"] == "https://api.groq.com"


def test_translate_legacy_fields_helper() -> None:
    translated = translate_legacy_llm_provider_fields(
        {"providers": {"x": {"api_key": "k", "base_url": "https://x"}}}
    )
    assert translated["providers"]["x"]["auth"]["api_key"] == "k"
    assert translated["providers"]["x"]["endpoint"] == "https://x"
    assert "api_key" not in translated["providers"]["x"]
    assert "base_url" not in translated["providers"]["x"]


def test_restore_api_key_sentinels_helper() -> None:
    merged = {
        "providers": {
            "groq": {"auth": {"api_key": "***"}},
            "new": {"auth": {"api_key": "***"}},
        }
    }
    restore_api_key_sentinels(
        merged,
        {"groq": {"auth": {"api_key": "kept"}}},
    )
    assert merged["providers"]["groq"]["auth"]["api_key"] == "kept"
    assert merged["providers"]["new"]["auth"]["api_key"] is None


def test_legacy_flat_api_key_sentinel_preserves_stored_secret() -> None:
    current = UserConfig(
        llm=UserLlmConfig(
            providers={
                "groq": UserLlmProviderSettings(
                    type="groq",
                    auth=UserLlmProviderAuth(api_key="secret-key"),
                )
            }
        )
    )
    mutation = apply_settings_patch(
        current,
        {"llm": {"providers": {"groq": {"api_key": "***", "enabled": False}}}},
    )
    assert mutation.config.llm is not None
    assert mutation.config.llm.providers["groq"].auth.api_key == "secret-key"
    assert mutation.config.llm.providers["groq"].enabled is False


def test_null_clear_commit_applies_live_restore(tmp_path) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / ".murder").mkdir()
    (repo_dir / ".murder" / "roles.yaml").write_text(
        "project:\n  name: test\n",
        encoding="utf-8",
    )
    live = Config.load(repo_dir)
    live.collaborator.harness = "codex"
    live.planner.harness = "pi"
    live.default_crow.harness = "claude_code"
    live.default_crow.harnesses = ["claude_code", "codex"]

    repo = _FakeRepo(
        UserConfig(
            collaborator=UserHarnessRolePatch(harness="codex"),
            planner=UserHarnessRolePatch(harness="pi"),
            default_crow=UserHarnessRolePatch(
                harness="claude_code", harnesses=["claude_code", "codex"]
            ),
        )
    )
    fake_live = _FakeLive()
    # Drive through the real apply path after commit ordering.
    mutation = apply_settings_patch(
        repo.config,
        {
            "collaborator_harness": None,
            "planner_harness": None,
            "crow_harnesses": None,
        },
    )

    def _apply(changes: Any) -> None:
        fake_live.apply(changes)
        apply_live_changes(live, changes)

    class _LivePort:
        def apply(self, changes: Any) -> None:
            _apply(changes)

    commit_settings_mutation(mutation, repo, _LivePort())
    assert len(repo.saved) == 1
    assert fake_live.applied == [
        (
            ClearCollaboratorHarness(),
            ClearPlannerHarness(),
            ClearCrowHarnesses(),
        )
    ]
    collab_default, _ = bundled_role_selection("collaborator")
    planner_default, _ = bundled_role_selection("planner")
    crow_harness, crow_harnesses = bundled_role_selection("default_crow")
    assert live.collaborator.harness == collab_default
    assert live.planner.harness == planner_default
    assert live.default_crow.harness == crow_harness
    assert live.default_crow.harnesses == crow_harnesses


def test_persist_before_live_skips_live_when_save_fails() -> None:
    repo = _FakeRepo(UserConfig(), fail=True)
    live = _FakeLive()
    mutation = SettingsMutation(
        config=UserConfig(collaborator=UserHarnessRolePatch(harness="codex")),
        live_changes=(SetCollaboratorHarness("codex"),),
    )
    with pytest.raises(OSError, match="disk full"):
        commit_settings_mutation(mutation, repo, live)
    assert live.applied == []
    assert repo.saved == []


def test_persist_before_live_applies_after_save() -> None:
    repo = _FakeRepo(UserConfig())
    live = _FakeLive()
    mutation = apply_settings_patch(UserConfig(), {"collaborator_harness": "codex"})
    commit_settings_mutation(mutation, repo, live)
    assert len(repo.saved) == 1
    assert live.applied == [(SetCollaboratorHarness("codex"),)]


def test_clear_live_changes_restore_bundled_defaults(tmp_path) -> None:
    # Minimal live Config via bundled defaults path.
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".murder").mkdir()
    (repo / ".murder" / "roles.yaml").write_text(
        "project:\n  name: test\n",
        encoding="utf-8",
    )
    live = Config.load(repo)
    live.collaborator.harness = "codex"
    live.planner.harness = "pi"
    live.default_crow.harness = "claude_code"
    live.default_crow.harnesses = ["claude_code", "codex"]

    apply_live_changes(
        live,
        (
            ClearCollaboratorHarness(),
            ClearPlannerHarness(),
            ClearCrowHarnesses(),
        ),
    )
    collab_default, _ = bundled_role_selection("collaborator")
    planner_default, _ = bundled_role_selection("planner")
    crow_harness, crow_harnesses = bundled_role_selection("default_crow")
    assert live.collaborator.harness == collab_default
    assert live.planner.harness == planner_default
    assert live.default_crow.harness == crow_harness
    assert live.default_crow.harnesses == crow_harnesses


def test_settings_payload_projects_overrides_and_effective() -> None:
    cfg = UserConfig(
        collaborator=UserHarnessRolePatch(harness="codex"),
        llm=UserLlmConfig(
            providers={
                "groq": UserLlmProviderSettings(
                    type="groq",
                    auth=UserLlmProviderAuth(api_key="secret"),
                )
            }
        ),
    )
    payload = build_settings_payload(
        cfg,
        effective=EffectiveHarnessView(
            collaborator="codex",
            planner="claude_code",
            crow=("cursor",),
        ),
        llm_env={"groq": True, "cerebras": False, "openrouter": False},
    )
    assert payload["collaborator_harness"] == "codex"
    assert payload["planner_harness"] is None
    assert payload["crow_harnesses"] is None
    assert payload["effective_collaborator_harness"] == "codex"
    assert payload["effective_planner_harness"] == "claude_code"
    assert payload["effective_crow_harnesses"] == ["cursor"]
    assert payload["llm"]["providers"]["groq"]["auth"]["api_key"] == "***"
    assert payload["llm_env"]["groq"] is True
    assert "project" not in payload


def test_settings_payload_includes_project_when_provided() -> None:
    payload = build_settings_payload(
        UserConfig(),
        effective=EffectiveHarnessView(
            collaborator="claude_code",
            planner="claude_code",
            crow=("claude_code",),
        ),
        project="murder",
    )
    assert payload["project"] == "murder"


def test_background_transparency_patch_and_payload() -> None:
    mutation = apply_settings_patch(UserConfig(), {"background_transparency": 50})
    assert mutation.config.tui.background_transparency == 50
    payload = build_settings_payload(
        mutation.config,
        effective=EffectiveHarnessView(
            collaborator="claude_code",
            planner="claude_code",
            crow=("claude_code",),
        ),
    )
    assert payload["background_transparency"] == 50
    assert UserConfig().tui.background_transparency == 100
