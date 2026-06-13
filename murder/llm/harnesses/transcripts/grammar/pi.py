"""Pi harness grammar plugin."""

from __future__ import annotations

import re

from murder.llm.harnesses.parsing import is_rule_line, is_status_spinner_line
from murder.llm.harnesses.transcripts.segments import Segment, SpannedSegment
from murder.llm.harnesses.transcripts._shared import (
    dedupe_adjacent_spanned,
    strip_leading_system_prompt,
)
from murder.llm.harnesses.transcripts.toolkit import collect_chrome_delimited_blocks

# ---- pi regexes ------------------------------------------------------------ #
# The pi *chrome* predicate and its regexes live here (the grammar owns them); the
# pi_harness adapter imports ``_is_pi_chrome`` / ``_PI_REASONING_PREFIX_RE`` back
# from this module. Layering: adapter→grammar is allowed, grammar→adapter is not.
_PI_STATUS_RE = re.compile(r"\b\d+(?:\.\d+)?%/\d+(?:\.\d+)?[kKmM]\s+\([^)]*\)")
_PI_CWD_RE = re.compile(r"^(?:~/|/|\./|\.\./).*(?:\s+\([^)]+\))?$")
_PI_CHROME_RE = re.compile(
    r"""
    ^\s*(?:
        pi\s+v\d+\b
        |escape\s+interrupt\b
        |press\s+ctrl\+o\b
        |pi\s+can\s+explain\b
        |extend\s+pi\.
        |warning:\s+tmux\s+extended-keys\b
        |[>\s]*set\b.*\bextended\s+keys\s+(?:on|off)\b
        |scope:\s+all\s+\|\s+scoped\b
        |tab\s+scope\b
        |model\s+scope:
        |model\s+name:
        |.*ctrl\+p\s+to\s+cycle\b
        |.*\brestart\s+tmux\.?\s*$
        |use\s+/login\b
        |.*docs/(?:providers|models)\.md\s*$
        |update\s+available\b
        |new\s+version\s+\d
        |changelog:\s+https?://
        |https?://\S
        |.*\.(?:g|gg|ggu|gguf|bin|safetensors)\b
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)
_PI_REASONING_PREFIX_RE = re.compile(
    r"""
    ^\s*(?:
        the\s+user\s+(?:wants|asked|is\s+asking)\b
        |i\s+(?:need|should|will|can)\b
        |we\s+need\b
        |need\s+to\b
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)
_PI_COMPACTION_RE = re.compile(
    r"^\s*(?:\[compaction\]|compacted\s+from\b|run\s+pi\s+update\b|changelog:\s*$|more\s*$)",
    re.IGNORECASE,
)
_PI_URL_FRAG_RE = re.compile(r"^\s*[A-Z]{2,}[a-zA-Z0-9_-]*\.[a-z]{1,5}\s*$")


def _is_pi_chrome(line: str) -> bool:
    """True if a pane line is pi UI chrome (status bar, cwd, banner, hints)."""
    s = line.strip()
    if not s:
        return False
    if is_rule_line(line) or is_status_spinner_line(line):
        return True
    return bool(_PI_STATUS_RE.search(s) or _PI_CWD_RE.match(s) or _PI_CHROME_RE.match(s))


def _pi_is_transcript_chrome(line: str) -> bool:
    """Chrome predicate for pi transcript parsing."""
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
    user_texts: list[str] | None = None,
) -> list[Segment]:
    return [s.segment for s in parse_spanned(lines, system_prompt, user_texts)]


def parse_spanned(
    lines: list[str],
    system_prompt: str | None = None,
    user_texts: list[str] | None = None,  # noqa: ARG001
) -> list[SpannedSegment]:
    """Parse pi scrollback into span-annotated segments.

    Pi uses no syntactic user/assistant markers. Submitted content in
    scrollback is indented with a single leading space.
    """
    blocks = collect_chrome_delimited_blocks(lines, _pi_is_transcript_chrome)

    # strip_leading_system_prompt operates on the bare block bodies; apply it to
    # the bodies and slice the matching prefix off the span-carrying blocks.
    bodies = [body for body, _start, _end in blocks]
    kept_bodies = strip_leading_system_prompt(bodies, system_prompt)
    blocks = blocks[len(bodies) - len(kept_bodies):]

    spanned: list[SpannedSegment] = []
    i = 0
    while i < len(blocks):
        user_body, user_start, user_end = blocks[i]
        user_text = " ".join(l.strip() for l in user_body if l.strip())
        if not user_text:
            i += 1
            continue
        spanned.append(SpannedSegment({"type": "user", "text": user_text}, user_start, user_end))
        i += 1

        assistant_parts: list[str] = []
        a_start = a_end = -1
        while i < len(blocks):
            body, start, end = blocks[i]
            block_text = " ".join(l.strip() for l in body if l.strip())
            if not block_text:
                i += 1
                continue
            if _PI_REASONING_PREFIX_RE.match(block_text):
                i += 1
                continue
            if not assistant_parts:
                assistant_parts.append(block_text)
                a_start, a_end = start, end
                i += 1
            else:
                break

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
                    a_end,
                )
            )

    return dedupe_adjacent_spanned(spanned)


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
