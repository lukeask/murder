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


def test_app_registers_everforest_and_has_settings_binding(monkeypatch, tmp_path) -> None:
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
    def _binding_key(b):
        return b.key if hasattr(b, "key") else b[0]

    def _binding_action(b):
        return b.action if hasattr(b, "action") else b[1]

    assert any(
        _binding_key(b) == "ctrl+p" and _binding_action(b) == "open_settings"
        for b in app.BINDINGS
    )
    assert any(
        _binding_key(b) == "f6" and _binding_action(b) == "kick_ready"
        for b in app.BINDINGS
    )
