"""Antigravity harness grammar plugin."""

from __future__ import annotations

import re

from murder.llm.harnesses.transcripts.segments import Segment
from murder.llm.harnesses.transcripts._shared import (
    _RULE_RE,
    _dedupe_adjacent,
)

# ---- antigravity regexes --------------------------------------------------- #
_AGY_PROMPT_RE = re.compile(r"^\s*>\s+(.+)$")
_AGY_THINKING_HEADER_RE = re.compile(r"^\s*▸\s+Thought\s+for\b")
_AGY_INTERRUPTED_RE = re.compile(r"^\s*⎿\s+Interrupted\b")
_AGY_LOGO_RE = re.compile(r"^\s*[▄▀]")


def _agy_is_chrome(line: str) -> bool:
    s = line.strip()
    if not s:
        return True
    if _RULE_RE.match(line):
        return True
    if _AGY_LOGO_RE.match(line):
        return True
    if _AGY_THINKING_HEADER_RE.match(line):
        return True
    if _AGY_INTERRUPTED_RE.match(line):
        return True
    lowered = s.lower()
    for drop in ("? for shortcuts", "esc to cancel", "↑/↓ navigate", "generating..."):
        if drop in lowered:
            return True
    return False


def _is_agy_live_prompt(lines: list[str], index: int) -> bool:
    """True when a `>` line is the live input box (between two horizontal rules)."""
    before = index - 1
    while before >= 0 and not lines[before].strip():
        before -= 1
    after = index + 1
    while after < len(lines) and not lines[after].strip():
        after += 1
    return (
        before >= 0
        and after < len(lines)
        and bool(_RULE_RE.match(lines[before]))
        and bool(_RULE_RE.match(lines[after]))
    )


def parse_lines(
    lines: list[str],
    system_prompt: str | None = None,  # noqa: ARG001
    user_texts: list[str] | None = None,  # noqa: ARG001
) -> list[Segment]:
    """Parse antigravity scrollback into segments."""
    segments: list[Segment] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        prompt = _AGY_PROMPT_RE.match(line)
        if prompt and not _is_agy_live_prompt(lines, i):
            user_text = prompt.group(1).strip()
            if user_text:
                segments.append({"type": "user", "text": user_text})
            i += 1
            assistant_parts: list[str] = []
            while i < len(lines):
                aline = lines[i]
                if _AGY_PROMPT_RE.match(aline) and not _is_agy_live_prompt(lines, i):
                    break
                if _is_agy_live_prompt(lines, i):
                    break
                if _AGY_THINKING_HEADER_RE.match(aline):
                    i += 1
                    while i < len(lines) and lines[i].startswith("  ") and lines[i].strip():
                        assistant_parts.append(lines[i].strip())
                        i += 1
                    continue
                if _agy_is_chrome(aline):
                    i += 1
                    continue
                stripped = aline.strip()
                if stripped:
                    assistant_parts.append(stripped)
                i += 1
            if assistant_parts:
                segments.append(
                    {
                        "type": "assistant",
                        "phase": "intermediate",
                        "text": " ".join(assistant_parts),
                        "elapsed": None,
                    }
                )
            continue
        i += 1
    return _dedupe_adjacent(segments)


def is_idle(pane_text: str) -> bool:
    """True when the antigravity pane is awaiting input."""
    from murder.llm.harnesses.antigravity import AntigravityAdapter  # noqa: PLC0415

    return AntigravityAdapter().is_idle(pane_text)


def detect_live_choice_prompt(frame: str) -> None:  # type: ignore[return]
    """Antigravity has no choice prompt UI."""
    return None


def close_last_turn(segments: list[Segment]) -> None:
    """At idle, mark all intermediate antigravity assistant blocks final."""
    for segment in segments:
        if segment["type"] == "assistant" and segment.get("phase") == "intermediate":
            segment["phase"] = "final"
