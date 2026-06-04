"""Custom Textual themes."""

from __future__ import annotations

from dataclasses import replace

from textual.theme import BUILTIN_THEMES, Theme

# Based on Kitty "Everforest Dark Hard" by Sainnhe Park.
# License: MIT
# Upstream:
# https://github.com/ewal/kitty-everforest/blob/master/themes/everforest_dark_hard.conf
EVERFOREST_DARK_HARD = Theme(
    name="everforest-dark-hard",
    primary="#a7c080",
    secondary="#7fbbb3",
    warning="#dbbc7f",
    error="#e67e80",
    success="#83c092",
    accent="#e69875",
    foreground="#d3c6aa",
    background="#21282d",
    surface="#272e33",
    panel="#2e383c",
    dark=True,
    variables={
        "border": "#83c092",
        "border-blurred": "#374a40",
        "block-cursor-background": "#d3c6aa",
        "block-cursor-foreground": "#272e33",
        "button-color-foreground": "#272e33",
        "footer-background": "#2e383c",
        "footer-key-foreground": "#a7c080",
        "input-cursor-background": "#d3c6aa",
        "input-cursor-foreground": "#2e383c",
        "input-selection-background": "#464e53",
        "scrollbar": "#7fbbb3",
        "scrollbar-background": "#374145",
    },
)

CUSTOM_THEMES = (EVERFOREST_DARK_HARD,)


def crow_theme_variables(theme: Theme) -> dict[str, str]:
    """Semantic tokens for pane focus vs crow health borders.

    ``pane-focus`` is chosen to avoid every crow-health color so an active
    tile/roster row never reads as a status hue.
    """
    variables = theme.variables or {}
    health = {
        theme.error,
        theme.warning,
        theme.success,
        variables.get("border"),
        variables.get("border-blurred"),
    }
    health.discard(None)
    pane_focus: str | None = None
    for candidate in (theme.secondary, theme.accent, theme.primary):
        if candidate and candidate not in health:
            pane_focus = candidate
            break
    if pane_focus is None:
        pane_focus = theme.foreground or "#ffffff"

    neutral = variables.get("border-blurred") or variables.get("border") or theme.panel or theme.surface
    if neutral is None or neutral in {theme.success, theme.warning, theme.error}:
        neutral = theme.panel or theme.surface or "#555555"

    return {
        "pane-focus": pane_focus,
        "crow-health-red": theme.error or "#ff0000",
        "crow-health-yellow": theme.warning or "#ffff00",
        "crow-health-green": theme.success or "#00ff00",
        "crow-health-neutral": neutral,
    }


def crow_tui_variable_defaults() -> dict[str, str]:
    """Fallback CSS variables so widgets parse before a theme is selected."""
    return crow_theme_variables(BUILTIN_THEMES["textual-dark"])


def theme_with_crow_variables(theme: Theme) -> Theme:
    """Return ``theme`` with crow TUI tokens merged into ``variables``."""
    merged = {**(theme.variables or {}), **crow_theme_variables(theme)}
    return replace(theme, variables=merged)


def register_crow_themes(app: object) -> None:
    """Register custom + built-in themes, each augmented with crow tokens."""
    register = getattr(app, "register_theme")
    seen: set[str] = set()
    for theme in (*CUSTOM_THEMES, *BUILTIN_THEMES.values()):
        if theme.name in seen:
            continue
        seen.add(theme.name)
        register(theme_with_crow_variables(theme))
