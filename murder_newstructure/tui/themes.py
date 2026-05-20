"""Custom Textual themes."""

from __future__ import annotations

from textual.theme import Theme

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
