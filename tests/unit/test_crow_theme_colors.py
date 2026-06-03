"""Crow TUI tokens — pane focus must not reuse health hues."""

from __future__ import annotations

from textual.theme import BUILTIN_THEMES

from murder.tui.themes import CUSTOM_THEMES, crow_theme_variables, theme_with_crow_variables


def test_pane_focus_is_distinct_from_crow_health_on_all_themes() -> None:
    themes = {t.name: t for t in CUSTOM_THEMES}
    themes.update(BUILTIN_THEMES)
    for name, theme in themes.items():
        tokens = crow_theme_variables(theme)
        health = {
            tokens["crow-health-red"],
            tokens["crow-health-yellow"],
            tokens["crow-health-green"],
            tokens["crow-health-neutral"],
        }
        assert tokens["pane-focus"] not in health, name


def test_everforest_neutral_health_is_not_green() -> None:
    from murder.tui.themes import EVERFOREST_DARK_HARD

    tokens = crow_theme_variables(EVERFOREST_DARK_HARD)
    assert tokens["crow-health-neutral"] != tokens["crow-health-green"]
    assert tokens["crow-health-neutral"] == "#374a40"


def test_theme_with_crow_variables_merges_into_registered_theme() -> None:
    base = BUILTIN_THEMES["textual-dark"]
    merged = theme_with_crow_variables(base)
    assert merged.variables["pane-focus"]
    assert merged.variables["crow-health-red"] == base.error
