"""User-level config persistence."""

from __future__ import annotations

import yaml

from murder.user_config import UserConfig, config_path, load_user_config, save_user_config


def test_config_path_uses_xdg_config_home(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    assert config_path() == tmp_path / "murder" / "config.yaml"


def test_load_missing_user_config_returns_defaults(tmp_path) -> None:
    assert load_user_config(tmp_path / "missing.yaml") == UserConfig()


def test_save_and_load_theme(tmp_path) -> None:
    path = tmp_path / "murder" / "config.yaml"
    config = UserConfig()
    config.tui.theme = "everforest-dark-hard"

    save_user_config(config, path)

    assert load_user_config(path).tui.theme == "everforest-dark-hard"
    assert yaml.safe_load(path.read_text(encoding="utf-8")) == {
        "tui": {"theme": "everforest-dark-hard"}
    }
