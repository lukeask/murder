"""Pure helper functions shared across grammar plugins.

No imports from murder.  Grammars import from here; core imports from here.
Neither core nor grammars import each other, breaking any intra-package cycle.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Callable

from murder.llm.harnesses.transcripts.segments import Segment

# Shared chrome
_RULE_RE = re.compile(r"^\s*[─━═]{8,}\s*$")

_TITLE_MAX = 160


def truncate_title(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > _TITLE_MAX:
        text = text[: _TITLE_MAX - 1].rstrip() + "…"
    return text


def normalize_codex_text(text: str) -> str:
    return (
        text.replace("‘", "'")
        .replace("’", "'")
        .replace("“", '"')
        .replace("”", '"')
    )


def normalize_prompt_match(text: str) -> str:
    """Fold a string to a comparison form for system-prompt matching."""
    return " ".join(normalize_codex_text(text).split())


def murder_owned_anchors(
    system_prompt: str | None, user_texts: list[str] | None
) -> set[str]:
    """Normalised text of every block murder itself authored: the injected
    system prompt (split into its blank-line paragraphs, since markerless panes
    render each paragraph as its own block) and every ground-truth user turn.

    A parsed block whose normalised text is one of these is murder's own
    content echoed back by the harness — drop it rather than show it as chat.
    """
    anchors: set[str] = set()
    if system_prompt:
        for para in re.split(r"\n\s*\n", system_prompt):
            normalized = normalize_prompt_match(para)
            if normalized:
                anchors.add(normalized)
    for text in user_texts or ():
        normalized = normalize_prompt_match(text)
        if normalized:
            anchors.add(normalized)
    return anchors


def reflow_paragraphs(
    lines: list[str],
    *,
    dedent: Callable[[str], str],
    preserve_prefixes: tuple[str, ...],
    preserve_strip: bool,
    post: Callable[[str], str] = lambda text: text,
) -> str:
    """De-wrap prose into paragraphs; preserve tables / lists / diffs verbatim."""
    cleaned = [dedent(line) for line in lines]
    while cleaned and not cleaned[0].strip():
        cleaned.pop(0)
    while cleaned and not cleaned[-1].strip():
        cleaned.pop()

    paragraphs: list[list[str]] = []
    current: list[str] = []
    for line in cleaned:
        if not line.strip():
            if current:
                paragraphs.append(current)
                current = []
            continue
        current.append(line)
    if current:
        paragraphs.append(current)

    rendered: list[str] = []
    for paragraph in paragraphs:
        preserve = any(
            line.lstrip().startswith(preserve_prefixes) or re.match(r"^\s*\d+\.\s", line)
            for line in paragraph
        )
        if preserve:
            if preserve_strip:
                rendered.append("\n".join(line.strip() for line in paragraph))
            else:
                rendered.append("\n".join(paragraph))
        else:
            rendered.append(" ".join(line.strip() for line in paragraph))
    return post("\n\n".join(rendered).strip())


def _is_streaming_extension(a: str, b: str) -> bool:
    """True when one text is a prefix of the other (a block that grew between frames)."""
    return a.startswith(b) or b.startswith(a)


def _segment_key(segment: Segment) -> tuple:
    """A stable identity for a segment that survives streaming growth."""
    if segment["type"] == "user":
        return ("user", segment["text"][:48])
    if segment["type"] == "assistant":
        return ("assistant", segment["text"][:48])
    if segment["type"] == "tool_call":
        return ("tool_call", segment["title"])
    if segment["type"] == "plan_update":
        return ("plan_update", sum(1 for it in segment["items"] if it["done"]), len(segment["items"]))
    if segment["type"] == "agent_event":
        return ("agent_event", segment["name"], segment["status"])
    option_numbers = tuple(option["number"] for option in segment["options"])
    return ("choice_prompt", segment["question"], option_numbers)


def _dedupe_adjacent(segments: list[Segment]) -> list[Segment]:
    """Collapse a block's streaming re-renders into one segment.

    Two adjacent segments can be two renders of one logical event:
    - A tool rendered first in a pending form then a completed form.
    - An assistant block captured truncated then grown; keep the longer.
    Byte-identical neighbours collapse too. Distinct non-adjacent turns are
    never merged.
    """
    result: list[Segment] = []
    for segment in segments:
        if result and segment == result[-1]:
            continue
        prev = result[-1] if result else None
        if (
            prev is not None
            and segment["type"] == "tool_call"
            and prev["type"] == "tool_call"
            and segment["title"] == prev["title"]
        ):
            if segment.get("result") is None and prev.get("result") is not None:
                merged_tool = prev
            else:
                merged_tool = segment
            merged_tool["elided"] = bool(prev.get("elided") or segment.get("elided"))
            result[-1] = merged_tool
            continue
        if (
            prev is not None
            and segment["type"] == "assistant"
            and prev["type"] == "assistant"
            and _is_streaming_extension(prev["text"], segment["text"])
        ):
            longer = segment if len(segment["text"]) >= len(prev["text"]) else prev
            if prev.get("phase") == "final" or segment.get("phase") == "final":
                longer["phase"] = "final"
            longer["elapsed"] = prev.get("elapsed") or segment.get("elapsed")
            result[-1] = longer
            continue
        if (
            prev is not None
            and segment["type"] == "choice_prompt"
            and prev["type"] == "choice_prompt"
            and _segment_key(segment) == _segment_key(prev)
        ):
            replacement = copy.deepcopy(segment)
            replacement["answered"] = bool(prev.get("answered") or segment.get("answered"))
            replacement["chosen"] = (
                prev.get("chosen") if prev.get("chosen") is not None else segment.get("chosen")
            )
            result[-1] = replacement
            continue
        result.append(segment)
    return result


def strip_leading_system_prompt(
    blocks: list[list[str]], system_prompt: str | None
) -> list[list[str]]:
    """Drop the leading blocks that reconstruct murder's injected system prompt.

    Markerless harnesses assign roles by alternating block position, so the
    crow's injected system prompt — echoed as a user turn the harness never
    answers — inverts every subsequent role. Murder owns the exact prompt text,
    so we recognise the leading blocks that form it and drop them, letting
    alternation restart from the first real user message.

    The prompt spans multiple blank-line-separated paragraphs, so we consume as
    many leading blocks as form a (normalised) prefix of the prompt and only
    strip them once they cover the *whole* prompt. The all-or-nothing rule is
    deliberate: a merely partial match (the prompt's head scrolled out of the
    capture, or a character we failed to normalise) leaves every block intact.
    """
    if not system_prompt or not blocks:
        return blocks
    target = normalize_prompt_match(system_prompt)
    if not target:
        return blocks
    acc = ""
    for consumed, block in enumerate(blocks, start=1):
        block_text = " ".join(line.strip() for line in block if line.strip())
        acc = normalize_prompt_match(f"{acc} {block_text}")
        if acc == target:
            return blocks[consumed:]
        if not target.startswith(acc):
            break
    return blocks
