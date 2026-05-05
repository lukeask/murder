"""Custom TUI theme definitions."""

from __future__ import annotations

from types import SimpleNamespace

import yaml

from murder.tui.app import MurderApp
from murder.tui.themes import EVERFOREST_DARK_HARD


def test_everforest_theme_is_selectable_textual_theme() -> None:
    assert EVERFOREST_DARK_HARD.name == "everforest-dark-hard"
    assert EVERFOREST_DARK_HARD.dark is True
    assert EVERFOREST_DARK_HARD.background == "#21282d"
    assert EVERFOREST_DARK_HARD.primary == "#a7c080"


def test_app_registers_everforest_and_persists_theme(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    runtime = SimpleNamespace(
        config=SimpleNamespace(
            project=SimpleNamespace(name="test"),
            tui=SimpleNamespace(refresh_ms=1000),
        ),
        repo_root=tmp_path,
        db=None,
    )
    app = MurderApp(runtime)  # type: ignore[arg-type]

    assert "everforest-dark-hard" in app.available_themes
    assert any(
        binding[0] == "ctrl+p" and binding[1] == "change_theme"
        for binding in app.BINDINGS
    )
    assert any(
        binding[0] == "f6" and binding[1] == "kick_ready"
        for binding in app.BINDINGS
    )

    app._persist_theme_changes = True
    app._watch_theme("everforest-dark-hard")

    path = tmp_path / "murder" / "config.yaml"
    assert yaml.safe_load(path.read_text(encoding="utf-8")) == {
        "tui": {"theme": "everforest-dark-hard"}
    }
