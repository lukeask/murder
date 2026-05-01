"""Parse Send() key strings into tmux send-keys segments."""

from __future__ import annotations

import re
from typing import Literal

Segment = tuple[Literal["literal", "key"], str]

# Angle-bracket tokens → tmux send-keys key names (no -l).
_TOKEN_TO_TMUX: dict[str, str] = {
    "Enter": "Enter",
    "Esc": "Escape",
    "Tab": "Tab",
    "BTab": "BTab",
    "Space": "Space",
    "BS": "BSpace",
    "Up": "Up",
    "Down": "Down",
    "Left": "Left",
    "Right": "Right",
    "Home": "Home",
    "End": "End",
    "PageUp": "PageUp",
    "PageDown": "PageDown",
    "NPage": "NPage",
    "PPage": "PPage",
    "DC": "DC",
    "IC": "IC",
    "Delete": "DC",
    "Insert": "IC",
    **{f"F{n}": f"F{n}" for n in range(1, 13)},
}

_CTRL_RE = re.compile(r"^C-(.)$", re.IGNORECASE)
_META_RE = re.compile(r"^M-(.)$", re.IGNORECASE)


def _tmux_key_name(token: str) -> str | None:
    t = token.strip()
    if not t:
        return None
    if t in _TOKEN_TO_TMUX:
        return _TOKEN_TO_TMUX[t]
    m = _CTRL_RE.match(t)
    if m:
        ch = m.group(1).upper()
        if len(ch) == 1 and (ch.isalpha() or ch in "@[]\\^_"):
            return f"C-{ch.lower() if ch.isalpha() else ch}"
    m = _META_RE.match(t)
    if m:
        ch = m.group(1)
        if len(ch) == 1:
            return f"M-{ch}"
    return None


def parse_send_keys(keys: str) -> tuple[list[Segment], str | None]:
    """
    Returns (segments, error_message).
    On error, segments may be partial; caller should ignore and return error.
    """
    out: list[Segment] = []
    i = 0
    lit_start = 0

    def flush_literal(end: int) -> None:
        if end > lit_start:
            out.append(("literal", keys[lit_start:end]))

    while i < len(keys):
        if keys[i] != "<":
            i += 1
            continue
        flush_literal(i)
        j = keys.find(">", i + 1)
        if j < 0:
            return out, f'error: malformed key token "{keys[i:]}" in keys'
        raw = keys[i + 1 : j]
        name = _tmux_key_name(raw)
        if name is None:
            return out, f'error: malformed key token "<{raw}>" in keys'
        out.append(("key", name))
        i = j + 1
        lit_start = i

    flush_literal(len(keys))
    return out, None
