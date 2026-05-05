from __future__ import annotations

import re

_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_UI_CHROME_RE = re.compile(
    r"""
    ^\s*(?:
        Composer\s+\d+\b.*(?:Auto-run|files?\s+edited|%)
        |ctrl\+r\s+to\s+review\s+edits\b.*
        |Auto-run\s*$
        |\u2192\s*Add\s+a\s+follow-up\s*$
        |\u256d.*\u256e\s*$
        |\u2570.*\u256f\s*$
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


def strip_ansi(s: str) -> str:
    return _ANSI_RE.sub("", s)


def strip_ui_chrome(s: str) -> str:
    """Remove known harness status/footer lines before sentinel parsing."""
    clean = strip_ansi(s)
    return "\n".join(
        line for line in clean.splitlines() if not _UI_CHROME_RE.match(line.strip())
    )


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
