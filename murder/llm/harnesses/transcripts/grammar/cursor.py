"""Cursor harness grammar plugin."""

from __future__ import annotations

import re

from murder.llm.harnesses.transcripts.segments import Segment
from murder.llm.harnesses.transcripts._shared import (
    _dedupe_adjacent,
    strip_leading_system_prompt,
)

# ---- cursor regexes -------------------------------------------------------- #
_CURSOR_INPUT_LINE_RE = re.compile(r"^\s*→\s*\S")
_CURSOR_STARTUP_HINT_RE = re.compile(r"^(?:Use\s+/\S|Try\s+Composer\b)", re.IGNORECASE)


def _cursor_is_chrome(line: str) -> bool:
    """Chrome predicate for cursor transcript parsing.

    Extends `_is_cursor_chrome` from cursor.py with patterns that are not
    needed for idle/busy state detection but must be suppressed in the parsed
    transcript.
    """
    from murder.llm.harnesses.cursor import _is_cursor_chrome  # noqa: PLC0415

    s = line.strip()
    if not s:
        return True
    if _is_cursor_chrome(line):
        return True
    if _CURSOR_INPUT_LINE_RE.match(line):
        return True
    if s.startswith("Tip:") or _CURSOR_STARTUP_HINT_RE.match(s):
        return True
    if not line.startswith(" "):
        return True
    return False


def parse_lines(lines: list[str], system_prompt: str | None = None) -> list[Segment]:
    """Parse cursor scrollback into segments.

    Cursor uses no syntactic markers. Content blocks alternate strictly
    user → assistant → user → …, starting with user.
    """
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if _cursor_is_chrome(line):
            if current:
                blocks.append(current)
                current = []
        else:
            current.append(line)
    if current:
        blocks.append(current)

    blocks = strip_leading_system_prompt(blocks, system_prompt)

    segments: list[Segment] = []
    for idx, block in enumerate(blocks):
        text = " ".join(line.strip() for line in block if line.strip())
        if not text:
            continue
        if idx % 2 == 0:
            segments.append({"type": "user", "text": text})
        else:
            segments.append(
                {"type": "assistant", "phase": "intermediate", "text": text, "elapsed": None}
            )
    return _dedupe_adjacent(segments)


def is_idle(pane_text: str) -> bool:
    """True when the cursor pane is awaiting input."""
    from murder.llm.harnesses.cursor import CursorAdapter  # noqa: PLC0415

    return CursorAdapter().is_idle(pane_text)


def detect_live_choice_prompt(frame: str) -> None:  # type: ignore[return]
    """Cursor has no choice prompt UI."""
    return None


def close_last_turn(segments: list[Segment]) -> None:
    """At idle, all cursor assistant blocks are complete turns — mark all final."""
    for segment in segments:
        if segment["type"] == "assistant" and segment.get("phase") == "intermediate":
            segment["phase"] = "final"
