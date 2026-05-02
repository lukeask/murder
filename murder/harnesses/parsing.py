from __future__ import annotations

import re

_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def strip_ansi(s: str) -> str:
    return _ANSI_RE.sub("", s)


def extract_last_message_heuristic(pane_text: str, *, max_lines: int = 40) -> str | None:
    lines = [ln.rstrip() for ln in strip_ansi(pane_text).splitlines()]
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        return None
    block: list[str] = []
    for ln in reversed(lines[-max_lines:]):
        s = ln.strip()
        if not s:
            if block:
                break
            continue
        if s in (">", "$", "%", "#") or (len(s) == 1 and s in ">#$%"):
            if block:
                break
            continue
        block.append(ln)
    if not block:
        return None
    block.reverse()
    return "\n".join(block).strip() or None
