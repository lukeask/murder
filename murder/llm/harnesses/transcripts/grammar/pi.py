"""Pi harness grammar plugin."""

from __future__ import annotations

import re

from murder.llm.harnesses.transcripts.segments import Segment
from murder.llm.harnesses.transcripts._shared import (
    _dedupe_adjacent,
    strip_leading_system_prompt,
)

# ---- pi regexes ------------------------------------------------------------ #
_PI_COMPACTION_RE = re.compile(
    r"^\s*(?:\[compaction\]|compacted\s+from\b|run\s+pi\s+update\b|changelog:\s*$|more\s*$)",
    re.IGNORECASE,
)
_PI_URL_FRAG_RE = re.compile(r"^\s*[A-Z]{2,}[a-zA-Z0-9_-]*\.[a-z]{1,5}\s*$")


def _pi_is_transcript_chrome(line: str) -> bool:
    """Chrome predicate for pi transcript parsing."""
    from murder.llm.harnesses.pi_harness import _is_pi_chrome  # noqa: PLC0415

    s = line.strip()
    if not s:
        return True
    if _is_pi_chrome(line):
        return True
    if _PI_COMPACTION_RE.match(s):
        return True
    if _PI_URL_FRAG_RE.match(s):
        return True
    if not line.startswith(" "):
        return True
    return False


def parse_lines(
    lines: list[str],
    system_prompt: str | None = None,
    user_texts: list[str] | None = None,  # noqa: ARG001
) -> list[Segment]:
    """Parse pi scrollback into segments.

    Pi uses no syntactic user/assistant markers. Submitted content in
    scrollback is indented with a single leading space.
    """
    from murder.llm.harnesses.pi_harness import _PI_REASONING_PREFIX_RE  # noqa: PLC0415

    blocks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if _pi_is_transcript_chrome(line):
            if current:
                blocks.append(current)
                current = []
        else:
            current.append(line)
    if current:
        blocks.append(current)

    blocks = strip_leading_system_prompt(blocks, system_prompt)

    segments: list[Segment] = []
    i = 0
    while i < len(blocks):
        user_text = " ".join(l.strip() for l in blocks[i] if l.strip())
        if not user_text:
            i += 1
            continue
        segments.append({"type": "user", "text": user_text})
        i += 1

        assistant_parts: list[str] = []
        while i < len(blocks):
            block_text = " ".join(l.strip() for l in blocks[i] if l.strip())
            if not block_text:
                i += 1
                continue
            if _PI_REASONING_PREFIX_RE.match(block_text):
                i += 1
                continue
            if not assistant_parts:
                assistant_parts.append(block_text)
                i += 1
            else:
                break

        if assistant_parts:
            segments.append(
                {
                    "type": "assistant",
                    "phase": "intermediate",
                    "text": " ".join(assistant_parts),
                    "elapsed": None,
                }
            )

    return _dedupe_adjacent(segments)


def is_idle(pane_text: str) -> bool:
    """True when the pi pane is awaiting input."""
    from murder.llm.harnesses.pi_harness import PiAdapter  # noqa: PLC0415

    return PiAdapter().is_idle(pane_text)


def detect_live_choice_prompt(frame: str) -> None:  # type: ignore[return]
    """Pi has no choice prompt UI."""
    return None


def close_last_turn(segments: list[Segment]) -> None:
    """At idle, mark all intermediate pi assistant blocks final."""
    for segment in segments:
        if segment["type"] == "assistant" and segment.get("phase") == "intermediate":
            segment["phase"] = "final"
