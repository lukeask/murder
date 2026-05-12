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


_TOOL_GLYPH_RE = re.compile(r"^\s*[⏺⎿└╰├│•⤷]+\s*")


def _clean_assistant_line(line: str) -> str:
    """Strip a leading tool-call / continuation glyph (⏺ ⎿ • │ …) from a line.

    These are pure harness chrome around tool output; removing the prefix
    leaves the actual text without disturbing real indentation.
    """
    return _TOOL_GLYPH_RE.sub("", line.rstrip())


def parse_prompt_marker_transcript(
    pane_text: str,
    *,
    prompt_markers: tuple[str, ...],
    drop_substrings: tuple[str, ...] = (),
) -> list[tuple[str, str]]:
    """Generic CLI-harness transcript parser.

    A line whose stripped form is exactly one of ``prompt_markers`` (``>``,
    ``❯``, ``›`` …) or begins with one followed by a space is taken as the
    user's submitted prompt; the block of lines until the next such prompt is
    that turn's assistant/tool output. Lines before the first prompt (banner,
    MOTD) are dropped; lines containing any of ``drop_substrings`` (status
    bars) are dropped; the trailing empty prompt is dropped.

    Returns ``(role, text)`` turns with ``role`` in ``{"user", "assistant"}``,
    or ``[]`` if no prompt line is visible. This is a heuristic keyed to the
    common "echoed prompt + free-text reply" shape — adapters whose UI has
    cleaner structure should override ``HarnessAdapter.parse_transcript`` with
    something tighter, ideally fixture-tested against a real pane capture.
    """
    if not prompt_markers:
        return []

    lines = strip_ansi(pane_text).splitlines()
    lowered_drops = tuple(d.lower() for d in drop_substrings)

    def split_prompt(line: str) -> tuple[bool, str]:
        s = line.strip()
        for marker in prompt_markers:
            if s == marker:
                return True, ""
            if s.startswith(marker + " "):
                return True, s[len(marker) + 1 :].strip()
        return False, ""

    def is_chrome(line: str) -> bool:
        s = line.strip().lower()
        return bool(s) and any(d in s for d in lowered_drops)

    turns: list[tuple[str, str]] = []
    cur_user: str | None = None
    assistant_lines: list[str] = []
    seen_prompt = False

    def flush() -> None:
        if cur_user is None:
            return
        turns.append(("user", cur_user))
        body = "\n".join(assistant_lines).strip()
        if body:
            turns.append(("assistant", body))

    for line in lines:
        if is_chrome(line):
            continue
        is_prompt, prompt_text = split_prompt(line)
        if is_prompt:
            flush()
            seen_prompt = True
            cur_user = prompt_text or None  # bare/empty prompt → not a real turn
            assistant_lines = []
            continue
        if not seen_prompt:
            continue
        assistant_lines.append(_clean_assistant_line(line))

    flush()
    return turns


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
