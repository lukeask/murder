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
    assert reply["settings"] == {
        "theme": "everforest-dark",
        "modifier": "alt",
        "key_overrides": {},
        "pane_gap": 0,
    }


def test_settings_update_persists_and_roundtrips(repo_root: Path, xdg: Path) -> None:
    host = _host(repo_root)
    reply = _call(
        host,
        "settings.update",
        {"settings": {"modifier": "ctrl", "key_overrides": {"global.spawn": "x"}}},
    )
    assert reply["ok"] is True
    assert reply["settings"] == {
        "theme": "everforest-dark",
        "modifier": "ctrl",
        "key_overrides": {"global.spawn": "x"},
        "pane_gap": 0,
    }
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
    assert reply["settings"] == {
        "theme": "everforest-dark",
        "modifier": "both",
        "key_overrides": {},
        "pane_gap": 0,
    }


def test_settings_update_persists_pane_gap(repo_root: Path, xdg: Path) -> None:
    host = _host(repo_root)
    reply = _call(host, "settings.update", {"settings": {"pane_gap": 3}})
    assert reply["settings"]["pane_gap"] == 3
    again = _call(host, "settings.get", {})
    assert again["settings"]["pane_gap"] == 3


def test_settings_update_rejects_invalid_modifier(repo_root: Path, xdg: Path) -> None:
    host = _host(repo_root)
    with pytest.raises(ValidationError):  # the Literal["alt","ctrl","both"] rejects it
        _call(host, "settings.update", {"settings": {"modifier": "hyper"}})


def test_settings_update_rejects_out_of_range_pane_gap(repo_root: Path, xdg: Path) -> None:
    host = _host(repo_root)
    with pytest.raises(ValidationError):  # ge=0/le=4 rejects 5
        _call(host, "settings.update", {"settings": {"pane_gap": 5}})


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
