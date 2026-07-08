"""Phase 3 — host-side `settings.{get,update}` RPC handlers + user_config migration.

These exercise the stateless preference handlers registered on the ``ServiceHost`` so the
TUI never touches ``~/.config/murder/config.yaml`` directly: ``settings.get`` /
``settings.update`` round-trip the new clean ``TuiUserConfig`` schema, partial updates merge,
and a stale ``config.yaml`` carrying the OLD ``tui`` fields (``editor`` / free-form ``theme``)
still loads clean (pydantic drops unknown tui keys).

The handlers call ``load_user_config()`` / ``save_user_config()`` directly against the
XDG-resolved path; we point ``XDG_CONFIG_HOME`` at a tmp dir so each test is isolated.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from murder.app.service.host import ServiceHost
from murder.config import (
    Config,
    CrowHandlerConfig,
    HarnessRoleConfig,
    ProjectConfig,
)
from murder.user_config import config_path, load_user_config


def _host(repo_root: Path) -> ServiceHost:
    config = Config(
        project=ProjectConfig(name="repo"),
        collaborator=HarnessRoleConfig(harness="codex"),
        default_crow=HarnessRoleConfig(harness="codex"),
        crow_handler=CrowHandlerConfig(model="test-model"),
    )
    host = ServiceHost(config=config, repo_root=repo_root)
    host.register_default_rpc_handlers()
    return host


def _call(host: ServiceHost, method: str, body: dict) -> dict:
    return host._rpc_handlers[method](body)  # type: ignore[return-value]


@pytest.fixture
def xdg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point user-config resolution at an isolated tmp config home."""
    home = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home))
    return home


def test_settings_get_returns_defaults_when_no_config(repo_root: Path, xdg: Path) -> None:
    host = _host(repo_root)
    reply = _call(host, "settings.get", {})
    assert reply["ok"] is True
    s = reply["settings"]
    # Existing tui fields are unchanged.
    assert s["theme"] == "everforest-dark"
    assert s["modifier"] == "alt"
    assert s["key_overrides"] == {}
    assert s["pane_gap"] == 0
    assert s["workspace_count"] == 1
    assert s["vim_mode"] is False
    assert s["bar_widgets"] == {}
    # No user override -> None; effective comes from the live daemon config (codex).
    assert s["collaborator_harness"] is None
    assert s["planner_harness"] is None
    assert s["crow_harnesses"] is None
    assert s["effective_collaborator_harness"] == "codex"
    assert s["effective_planner_harness"] == "claude_code"
    assert s["effective_crow_harnesses"] == ["codex"]
    assert s["llm"] == {}
    assert set(s["llm_env"]) == {"groq", "cerebras", "openrouter"}


def test_settings_update_persists_and_roundtrips(repo_root: Path, xdg: Path) -> None:
    host = _host(repo_root)
    reply = _call(
        host,
        "settings.update",
        {"settings": {"modifier": "ctrl", "key_overrides": {"global.spawn": "x"}}},
    )
    assert reply["ok"] is True
    assert reply["settings"]["theme"] == "everforest-dark"
    assert reply["settings"]["modifier"] == "ctrl"
    assert reply["settings"]["key_overrides"] == {"global.spawn": "x"}
    assert reply["settings"]["pane_gap"] == 0
    # Persisted: a fresh get sees it, and the file actually exists.
    assert config_path().exists()
    again = _call(host, "settings.get", {})
    assert again["settings"]["modifier"] == "ctrl"
    assert again["settings"]["key_overrides"] == {"global.spawn": "x"}


def test_settings_update_is_partial_merge(repo_root: Path, xdg: Path) -> None:
    host = _host(repo_root)
    # First set modifier + an override.
    _call(host, "settings.update", {"settings": {"modifier": "both", "theme": "everforest-light"}})
    # Now update ONLY the theme; modifier must be preserved.
    reply = _call(host, "settings.update", {"settings": {"theme": "everforest-dark"}})
    assert reply["settings"]["theme"] == "everforest-dark"
    assert reply["settings"]["modifier"] == "both"
    assert reply["settings"]["key_overrides"] == {}
    assert reply["settings"]["pane_gap"] == 0


def test_settings_update_persists_pane_gap(repo_root: Path, xdg: Path) -> None:
    host = _host(repo_root)
    reply = _call(host, "settings.update", {"settings": {"pane_gap": 3}})
    assert reply["settings"]["pane_gap"] == 3
    again = _call(host, "settings.get", {})
    assert again["settings"]["pane_gap"] == 3


def test_settings_update_persists_workspace_count(repo_root: Path, xdg: Path) -> None:
    host = _host(repo_root)
    reply = _call(host, "settings.update", {"settings": {"workspace_count": 3}})
    assert reply["settings"]["workspace_count"] == 3
    again = _call(host, "settings.get", {})
    assert again["settings"]["workspace_count"] == 3


def test_settings_update_persists_vim_mode(repo_root: Path, xdg: Path) -> None:
    host = _host(repo_root)
    # Default is False.
    assert _call(host, "settings.get", {})["settings"]["vim_mode"] is False
    # Round-trips True and persists across a fresh get + reload.
    reply = _call(host, "settings.update", {"settings": {"vim_mode": True}})
    assert reply["settings"]["vim_mode"] is True
    again = _call(host, "settings.get", {})
    assert again["settings"]["vim_mode"] is True
    assert load_user_config().tui.vim_mode is True


def test_settings_update_vim_mode_partial_merge(repo_root: Path, xdg: Path) -> None:
    host = _host(repo_root)
    # Setting vim_mode must not disturb other tui fields, and vice versa.
    _call(host, "settings.update", {"settings": {"vim_mode": True}})
    reply = _call(host, "settings.update", {"settings": {"pane_gap": 2}})
    assert reply["settings"]["vim_mode"] is True
    assert reply["settings"]["pane_gap"] == 2


def test_settings_update_bar_widgets_persists_and_roundtrips(repo_root: Path, xdg: Path) -> None:
    host = _host(repo_root)
    reply = _call(
        host,
        "settings.update",
        {"settings": {"bar_widgets": {"hints": {"enabled": False, "placement": "bottom"}}}},
    )
    assert reply["settings"]["bar_widgets"] == {
        "hints": {"enabled": False, "placement": "bottom", "adaptive": True},
    }
    again = _call(host, "settings.get", {})
    assert again["settings"]["bar_widgets"]["hints"]["enabled"] is False
    cfg = load_user_config()
    assert cfg.tui.bar_widgets["hints"].enabled is False


def test_settings_update_bar_widgets_partial_merge(repo_root: Path, xdg: Path) -> None:
    host = _host(repo_root)
    _call(
        host,
        "settings.update",
        {"settings": {"bar_widgets": {"hints": {"enabled": False, "placement": "bottom"}}}},
    )
    reply = _call(
        host,
        "settings.update",
        {"settings": {"bar_widgets": {"hints": {"enabled": True}}}},
    )
    assert reply["settings"]["bar_widgets"]["hints"] == {
        "enabled": True,
        "placement": "bottom",
        "adaptive": True,
    }
    _call(host, "settings.update", {"settings": {"pane_gap": 1}})
    again = _call(host, "settings.get", {})
    assert again["settings"]["bar_widgets"]["hints"]["enabled"] is True
    assert again["settings"]["pane_gap"] == 1


def test_settings_update_bar_widgets_adaptive_roundtrips(repo_root: Path, xdg: Path) -> None:
    host = _host(repo_root)
    reply = _call(
        host,
        "settings.update",
        {
            "settings": {
                "bar_widgets": {
                    "hints": {"enabled": True, "placement": "bottom", "adaptive": False}
                }
            }
        },
    )
    assert reply["settings"]["bar_widgets"]["hints"] == {
        "enabled": True,
        "placement": "bottom",
        "adaptive": False,
    }
    again = _call(host, "settings.get", {})
    assert again["settings"]["bar_widgets"]["hints"]["adaptive"] is False
    cfg = load_user_config()
    assert cfg.tui.bar_widgets["hints"].adaptive is False


def test_settings_update_usage_bar_widget_harnesses_persist_and_roundtrip(
    repo_root: Path, xdg: Path
) -> None:
    host = _host(repo_root)
    reply = _call(
        host,
        "settings.update",
        {
            "settings": {
                "bar_widgets": {
                    "usage": {
                        "enabled": True,
                        "placement": "top",
                        "harnesses": ["codex", "claude_code"],
                    }
                }
            }
        },
    )
    assert reply["settings"]["bar_widgets"]["usage"] == {
        "enabled": True,
        "placement": "top",
        "adaptive": True,
        "harnesses": ["codex", "claude_code"],
    }
    cfg = load_user_config()
    assert cfg.tui.bar_widgets["usage"].harnesses == ["codex", "claude_code"]
    again = _call(host, "settings.get", {})
    assert again["settings"]["bar_widgets"]["usage"]["harnesses"] == ["codex", "claude_code"]


def test_settings_update_usage_bar_widget_empty_harnesses_means_all(
    repo_root: Path, xdg: Path
) -> None:
    host = _host(repo_root)
    _call(
        host,
        "settings.update",
        {
            "settings": {
                "bar_widgets": {
                    "usage": {"enabled": True, "placement": "top", "harnesses": ["codex"]}
                }
            }
        },
    )
    reply = _call(
        host,
        "settings.update",
        {"settings": {"bar_widgets": {"usage": {"harnesses": []}}}},
    )
    usage = reply["settings"]["bar_widgets"]["usage"]
    assert "harnesses" not in usage
    assert load_user_config().tui.bar_widgets["usage"].harnesses is None


def test_settings_update_usage_bar_widget_rejects_invalid_harness(
    repo_root: Path, xdg: Path
) -> None:
    host = _host(repo_root)
    with pytest.raises(ValueError, match="invalid bar widget harness"):
        _call(
            host,
            "settings.update",
            {
                "settings": {
                    "bar_widgets": {"usage": {"harnesses": ["not_a_harness"]}}
                }
            },
        )


# --- startup rogue RPC ---


def test_settings_get_startup_rogue_defaults_none(repo_root: Path, xdg: Path) -> None:
    host = _host(repo_root)
    assert _call(host, "settings.get", {})["settings"]["startup_rogue"] is None


def test_settings_get_startup_rogue_model_choices(repo_root: Path, xdg: Path) -> None:
    host = _host(repo_root)
    settings = _call(host, "settings.get", {})["settings"]
    cursor_models = settings["startup_rogue_models"]["cursor"]
    assert cursor_models[0] == {"id": "composer-2.5", "label": "Composer 2.5"}
    assert {"id": "auto", "label": "Auto"} in cursor_models
    assert settings["startup_rogue_efforts"]["cursor"] == ["slow", "fast"]


def test_settings_update_startup_rogue_persists_and_roundtrips(repo_root: Path, xdg: Path) -> None:
    host = _host(repo_root)
    reply = _call(
        host,
        "settings.update",
        {"settings": {"startup_rogue": {"harness": "claude_code", "model": "opus", "effort": "medium"}}},
    )
    assert reply["settings"]["startup_rogue"] == {
        "harness": "claude_code",
        "model": "opus",
        "effort": "medium",
    }
    # Persisted under tui and visible on a fresh get + reload.
    again = _call(host, "settings.get", {})
    assert again["settings"]["startup_rogue"]["model"] == "opus"
    sr = load_user_config().tui.startup_rogue
    assert sr is not None and sr.harness == "claude_code" and sr.effort == "medium"


def test_settings_update_startup_rogue_null_clears(repo_root: Path, xdg: Path) -> None:
    host = _host(repo_root)
    _call(host, "settings.update", {"settings": {"startup_rogue": {"harness": "codex", "model": ""}}})
    reply = _call(host, "settings.update", {"settings": {"startup_rogue": None}})
    assert reply["settings"]["startup_rogue"] is None
    assert load_user_config().tui.startup_rogue is None


def test_settings_update_startup_rogue_empty_effort_normalizes_to_none(
    repo_root: Path, xdg: Path
) -> None:
    host = _host(repo_root)
    reply = _call(
        host,
        "settings.update",
        {"settings": {"startup_rogue": {"harness": "cursor", "model": "", "effort": ""}}},
    )
    assert reply["settings"]["startup_rogue"] == {"harness": "cursor", "model": "", "effort": None}


def test_settings_update_startup_rogue_survives_other_tui_updates(repo_root: Path, xdg: Path) -> None:
    host = _host(repo_root)
    _call(
        host,
        "settings.update",
        {"settings": {"startup_rogue": {"harness": "claude_code", "model": "opus"}}},
    )
    # A subsequent unrelated tui update must not drop the startup rogue.
    reply = _call(host, "settings.update", {"settings": {"pane_gap": 2}})
    assert reply["settings"]["startup_rogue"]["model"] == "opus"
    assert reply["settings"]["pane_gap"] == 2


def test_settings_update_startup_rogue_rejects_invalid_harness(repo_root: Path, xdg: Path) -> None:
    host = _host(repo_root)
    with pytest.raises(ValueError, match="invalid startup_rogue harness"):
        _call(host, "settings.update", {"settings": {"startup_rogue": {"harness": "bogus"}}})


def test_settings_update_rejects_invalid_modifier(repo_root: Path, xdg: Path) -> None:
    host = _host(repo_root)
    with pytest.raises(ValidationError):  # the Literal["alt","ctrl","both"] rejects it
        _call(host, "settings.update", {"settings": {"modifier": "hyper"}})


def test_settings_update_rejects_out_of_range_pane_gap(repo_root: Path, xdg: Path) -> None:
    host = _host(repo_root)
    with pytest.raises(ValidationError):  # ge=0/le=4 rejects 5
        _call(host, "settings.update", {"settings": {"pane_gap": 5}})


def test_settings_update_rejects_out_of_range_workspace_count(repo_root: Path, xdg: Path) -> None:
    host = _host(repo_root)
    with pytest.raises(ValidationError):  # ge=1/le=9 rejects 0 and 10
        _call(host, "settings.update", {"settings": {"workspace_count": 0}})
    with pytest.raises(ValidationError):
        _call(host, "settings.update", {"settings": {"workspace_count": 10}})


def test_settings_update_rejects_non_object(repo_root: Path, xdg: Path) -> None:
    host = _host(repo_root)
    with pytest.raises(ValueError, match="requires a settings object"):
        _call(host, "settings.update", {"settings": ["not", "a", "dict"]})


def test_stale_yaml_with_old_tui_fields_loads_clean(repo_root: Path, xdg: Path) -> None:
    # A config.yaml from the OLD schema: tui.editor + a free-form tui.theme + a live patch block.
    cfg_path = config_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(
        "tui:\n"
        "  editor: nvim\n"
        "  theme: gruvbox\n"
        "collaborator:\n"
        "  harness: codex\n",
        encoding="utf-8",
    )
    # Direct load: unknown tui keys (editor) are dropped; theme survives (it's still a tui field);
    # the collaborator patch block is untouched.
    cfg = load_user_config()
    assert not hasattr(cfg.tui, "editor")
    assert cfg.tui.theme == "gruvbox"
    assert cfg.tui.modifier == "alt"
    assert cfg.collaborator is not None
    assert cfg.collaborator.harness == "codex"

    # And the RPC handler loads it without raising.
    host = _host(repo_root)
    reply = _call(host, "settings.get", {})
    assert reply["ok"] is True
    assert reply["settings"]["theme"] == "gruvbox"
    assert reply["settings"]["modifier"] == "alt"


# --- harness override RPC ---


def test_update_crow_harnesses_single_sets_harness_and_mutates_live(
    repo_root: Path, xdg: Path
) -> None:
    host = _host(repo_root)
    reply = _call(host, "settings.update", {"settings": {"crow_harnesses": ["cursor"]}})
    assert reply["settings"]["crow_harnesses"] == ["cursor"]
    # Persisted as scalar harness (single element -> harness=X, harnesses=None).
    cfg = load_user_config()
    assert cfg.default_crow is not None
    assert cfg.default_crow.harness == "cursor"
    assert cfg.default_crow.harnesses is None
    # Live daemon config mutated in place so new spawns pick it up.
    assert host.config.default_crow.harness == "cursor"
    assert host.config.default_crow.harnesses is None
    assert reply["settings"]["effective_crow_harnesses"] == ["cursor"]


def test_update_crow_harnesses_multi_sets_pool_and_mutates_live(
    repo_root: Path, xdg: Path
) -> None:
    host = _host(repo_root)
    reply = _call(
        host,
        "settings.update",
        {"settings": {"crow_harnesses": ["cursor", "claude_code"]}},
    )
    assert reply["settings"]["crow_harnesses"] == ["cursor", "claude_code"]
    cfg = load_user_config()
    assert cfg.default_crow.harness == "cursor"
    assert cfg.default_crow.harnesses == ["cursor", "claude_code"]
    assert host.config.default_crow.harness == "cursor"
    assert host.config.default_crow.harnesses == ["cursor", "claude_code"]
    assert reply["settings"]["effective_crow_harnesses"] == ["cursor", "claude_code"]


def test_update_crow_harnesses_null_clears_user_override(repo_root: Path, xdg: Path) -> None:
    host = _host(repo_root)
    _call(host, "settings.update", {"settings": {"crow_harnesses": ["cursor", "pi"]}})
    reply = _call(host, "settings.update", {"settings": {"crow_harnesses": None}})
    # User override cleared; effective falls back to the live daemon config.
    assert reply["settings"]["crow_harnesses"] is None
    cfg = load_user_config()
    assert cfg.default_crow.harness is None
    assert cfg.default_crow.harnesses is None


def test_update_collaborator_harness_sets_and_mutates_live(repo_root: Path, xdg: Path) -> None:
    host = _host(repo_root)
    reply = _call(host, "settings.update", {"settings": {"collaborator_harness": "claude_code"}})
    assert reply["settings"]["collaborator_harness"] == "claude_code"
    assert reply["settings"]["effective_collaborator_harness"] == "claude_code"
    assert host.config.collaborator.harness == "claude_code"
    assert load_user_config().collaborator.harness == "claude_code"


def test_update_planner_harness_sets_and_mutates_live(repo_root: Path, xdg: Path) -> None:
    host = _host(repo_root)
    reply = _call(host, "settings.update", {"settings": {"planner_harness": "codex"}})
    assert reply["settings"]["planner_harness"] == "codex"
    assert reply["settings"]["effective_planner_harness"] == "codex"
    assert host.config.planner.harness == "codex"
    assert load_user_config().planner.harness == "codex"


def test_update_crow_harnesses_rejects_invalid_harness(repo_root: Path, xdg: Path) -> None:
    host = _host(repo_root)
    with pytest.raises(ValueError, match="invalid crow harness"):
        _call(host, "settings.update", {"settings": {"crow_harnesses": ["not_a_harness"]}})


def test_update_crow_harnesses_rejects_empty_list(repo_root: Path, xdg: Path) -> None:
    host = _host(repo_root)
    with pytest.raises(ValueError, match="non-empty list"):
        _call(host, "settings.update", {"settings": {"crow_harnesses": []}})


def test_update_collaborator_harness_rejects_invalid(repo_root: Path, xdg: Path) -> None:
    host = _host(repo_root)
    with pytest.raises(ValueError, match="invalid collaborator harness"):
        _call(host, "settings.update", {"settings": {"collaborator_harness": "bogus"}})


def test_update_planner_harness_rejects_invalid(repo_root: Path, xdg: Path) -> None:
    host = _host(repo_root)
    with pytest.raises(ValueError, match="invalid planner harness"):
        _call(host, "settings.update", {"settings": {"planner_harness": "bogus"}})


# --- llm block RPC ---


def test_update_llm_persists_and_masks_api_key_on_get(repo_root: Path, xdg: Path) -> None:
    host = _host(repo_root)
    reply = _call(
        host,
        "settings.update",
        {
            "settings": {
                "llm": {
                    "providers": {"groq": {"api_key": "secret-key"}},
                    "tiers": {"fast": {"provider": "groq", "model": "m1"}},
                    "roles": {"crow": "fast"},
                }
            }
        },
    )
    # Returned payload masks the key.
    assert reply["settings"]["llm"]["providers"]["groq"]["api_key"] == "***"
    assert reply["settings"]["llm"]["tiers"]["fast"] == {
        "provider": "groq",
        "model": "m1",
        "auto_free": False,
    }
    assert reply["settings"]["llm"]["roles"] == {"crow": "fast"}
    # Stored value is the real key (unmasked on disk).
    cfg = load_user_config()
    assert cfg.llm.providers["groq"].api_key == "secret-key"


def test_update_llm_star_sentinel_keeps_stored_key(repo_root: Path, xdg: Path) -> None:
    host = _host(repo_root)
    _call(host, "settings.update", {"settings": {"llm": {"providers": {"groq": {"api_key": "real"}}}}})
    # A subsequent update sending "***" must NOT overwrite the stored key.
    _call(
        host,
        "settings.update",
        {"settings": {"llm": {"providers": {"groq": {"api_key": "***", "base_url": "http://x"}}}}},
    )
    cfg = load_user_config()
    assert cfg.llm.providers["groq"].api_key == "real"
    assert cfg.llm.providers["groq"].base_url == "http://x"


def test_update_llm_empty_string_clears_key(repo_root: Path, xdg: Path) -> None:
    host = _host(repo_root)
    _call(host, "settings.update", {"settings": {"llm": {"providers": {"groq": {"api_key": "real"}}}}})
    _call(host, "settings.update", {"settings": {"llm": {"providers": {"groq": {"api_key": ""}}}}})
    cfg = load_user_config()
    assert cfg.llm.providers["groq"].api_key == ""


def test_get_llm_env_reflects_environment(
    repo_root: Path, xdg: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "x")
    monkeypatch.delenv("CEREBRAS_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    host = _host(repo_root)
    s = _call(host, "settings.get", {})["settings"]
    assert s["llm_env"] == {"groq": True, "cerebras": False, "openrouter": False}
