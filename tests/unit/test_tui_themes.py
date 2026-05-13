"""Custom TUI theme definitions."""

from __future__ import annotations

from types import SimpleNamespace

from murder.tui.app import MurderApp
from murder.tui.themes import EVERFOREST_DARK_HARD
from murder.user_config import UserConfig


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


def test_settings_close_reload_updates_runtime_project_config(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    runtime = SimpleNamespace(
        config=SimpleNamespace(
            project=SimpleNamespace(name="before"),
            tui=SimpleNamespace(refresh_ms=1000),
        ),
        repo_root=tmp_path,
        db=None,
    )
    app = MurderApp(runtime)  # type: ignore[arg-type]

    reloaded_config = SimpleNamespace(
        project=SimpleNamespace(name="after"),
        tui=SimpleNamespace(refresh_ms=1000),
    )
    monkeypatch.setattr("murder.tui.app.Config.load", lambda repo: reloaded_config)
    monkeypatch.setattr("murder.tui.app.load_user_config", lambda: UserConfig())

    app._on_settings_closed(True)

    assert runtime.config is reloaded_config
    assert app._header.project == "after"


def test_schedule_view_hides_chat_input(monkeypatch, tmp_path) -> None:
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

    app._view = "planning"
    app._apply_mode()
    assert app._chat.display is True

    app._view = "crows"
    app._apply_mode()
    assert app._chat.display is True

    app._view = "schedule"
    app._apply_mode()
    assert app._chat.display is False
