"""Antigravity harness grammar plugin."""

from __future__ import annotations

import re

from murder.llm.harnesses.transcripts.segments import Segment, SpannedSegment
from murder.llm.harnesses.transcripts._shared import dedupe_adjacent_spanned
from murder.llm.harnesses.transcripts.toolkit import (
    BASE_CHROME_RULES,
    chrome_matcher,
    is_rule_sandwiched,
    regex_match_rule,
    regex_search_rule,
)

# ---- antigravity regexes --------------------------------------------------- #
_AGY_PROMPT_RE = re.compile(r"^\s*>\s+(.+)$")
_AGY_THINKING_HEADER_RE = re.compile(r"^\s*▸\s+Thought\s+for\b")
_AGY_INTERRUPTED_RE = re.compile(r"^\s*⎿\s+Interrupted\b")
_AGY_LOGO_RE = re.compile(r"^\s*[▄▀]")
# The startup banner's logo glyphs (`▄▀`) are caught by _AGY_LOGO_RE, but the
# plain `Antigravity CLI 1.0.2` version line carries no glyph and would leak into
# segments. The old adapter dropped it via a case-insensitive substring rule
# (`transcript_drop_substrings = ('antigravity cli', ...)`); restore that here.
_AGY_CLI_BANNER_RE = re.compile(r"antigravity cli", re.IGNORECASE)
_AGY_STATUS_HINTS = ("? for shortcuts", "esc to cancel", "↑/↓ navigate", "generating...")


def _agy_status_hint(line: str) -> bool:
    """A case-insensitive status-bar / hint line antigravity paints at the foot."""
    lowered = line.strip().lower()
    return any(hint in lowered for hint in _AGY_STATUS_HINTS)


# Antigravity chrome: shared base plus its logo, the "Thought for" thinking
# header, the "Interrupted" marker and its lower-bar status hints.
_agy_is_chrome = chrome_matcher(
    *BASE_CHROME_RULES,
    regex_match_rule(_AGY_LOGO_RE),
    regex_search_rule(_AGY_CLI_BANNER_RE),
    regex_match_rule(_AGY_THINKING_HEADER_RE),
    regex_match_rule(_AGY_INTERRUPTED_RE),
    _agy_status_hint,
)


def _is_agy_live_prompt(lines: list[str], index: int) -> bool:
    """True when a `>` line is the live input box (between two horizontal rules)."""
    return is_rule_sandwiched(lines, index)


def parse_lines(
    lines: list[str],
    system_prompt: str | None = None,
    user_texts: list[str] | None = None,
) -> list[Segment]:
    return [s.segment for s in parse_spanned(lines, system_prompt, user_texts)]


def parse_spanned(
    lines: list[str],
    system_prompt: str | None = None,  # noqa: ARG001
    user_texts: list[str] | None = None,  # noqa: ARG001
) -> list[SpannedSegment]:
    """Parse antigravity scrollback into span-annotated segments."""
    spanned: list[SpannedSegment] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        prompt = _AGY_PROMPT_RE.match(line)
        if prompt and not _is_agy_live_prompt(lines, i):
            user_start = i
            user_text = prompt.group(1).strip()
            if user_text:
                spanned.append(
                    SpannedSegment({"type": "user", "text": user_text}, user_start, i + 1)
                )
            i += 1
            assistant_parts: list[str] = []
            a_start = i
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
                spanned.append(
                    SpannedSegment(
                        {
                            "type": "assistant",
                            "phase": "intermediate",
                            "text": " ".join(assistant_parts),
                            "elapsed": None,
                        },
                        a_start,
                        i,
                    )
                )
            continue
        i += 1
    return dedupe_adjacent_spanned(spanned)


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
