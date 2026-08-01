"""Deterministic focused unified diffs for changed ledger evidence.

Format can change here without touching ledger persistence or subtraction
semantics. Never falls back to a whole-file diff.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass

from murder.context_compiler.ledger.policy import (
    FOCUSED_DIFF_CONTEXT_LINES,
    FOCUSED_DIFF_TRUNCATION_MARKER,
    MAX_FOCUSED_DIFF_CHARS,
)
from murder.context_compiler.models import LineRange


@dataclass(frozen=True, slots=True)
class FocusedDiffResult:
    """Bounded focused-diff payload ready for an ``EvidenceSegment``."""

    text: str
    truncated: bool
    unmappable: bool = False


def build_focused_diff(
    *,
    path: str,
    old_range: LineRange,
    old_text: str,
    new_range: LineRange,
    new_text: str,
    max_chars: int = MAX_FOCUSED_DIFF_CHARS,
    context_lines: int = FOCUSED_DIFF_CONTEXT_LINES,
) -> FocusedDiffResult:
    """Build a deterministic unified diff of two exact bounded excerpts.

    Header form::

        src/foo.py:54-90 -> src/foo.py:61-102

    Whitespace and blank lines are preserved. No color. When comparison is
    impossible (both sides empty in a useless way), returns ``unmappable=True``
    with empty text so callers can send current source instead.
    """
    if old_text == "" and new_text == "":
        return FocusedDiffResult(text="", truncated=False, unmappable=True)

    old_lines = _lines_for_diff(old_text)
    new_lines = _lines_for_diff(new_text)
    header = (
        f"{path}:{old_range.start_line}-{old_range.end_line} -> "
        f"{path}:{new_range.start_line}-{new_range.end_line}"
    )
    body_lines = list(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=f"{path}:{old_range.start_line}-{old_range.end_line}",
            tofile=f"{path}:{new_range.start_line}-{new_range.end_line}",
            n=context_lines,
            lineterm="",
        )
    )
    # Drop difflib's --- / +++ file headers; our path:range header replaces them.
    filtered = [line for line in body_lines if not line.startswith(("--- ", "+++ "))]
    payload = header + "\n" + "\n".join(filtered)
    if filtered:
        payload += "\n"

    if len(payload) <= max_chars:
        return FocusedDiffResult(text=payload, truncated=False, unmappable=False)

    keep = max_chars - len(FOCUSED_DIFF_TRUNCATION_MARKER)
    if keep < len(header) + 1:
        truncated = header + FOCUSED_DIFF_TRUNCATION_MARKER
    else:
        truncated = payload[:keep].rstrip("\n") + FOCUSED_DIFF_TRUNCATION_MARKER
    return FocusedDiffResult(text=truncated, truncated=True, unmappable=False)


def _lines_for_diff(text: str) -> list[str]:
    """Split excerpt text into lines without dropping a trailing blank line."""
    if text == "":
        return []
    # splitlines() drops a final bare newline; keep content lines as-is.
    return text.splitlines()


__all__ = [
    "FocusedDiffResult",
    "build_focused_diff",
]
