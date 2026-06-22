"""Pure helper functions shared across grammar plugins.

No imports from murder.  Grammars import from here; core imports from here.
Neither core nor grammars import each other, breaking any intra-package cycle.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Callable

from murder.llm.harnesses.transcripts.segments import Segment, SpannedSegment

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


# Box-drawing / block glyphs whose presence in a block marks it as preformatted
# (tables, frames, trees). Shared default; grammars may extend via box_chars.
_DEFAULT_BOX_CHARS = frozenset("┌┐└┘├┤┬┴┼─│┃━═╋╔╗╚╝║╠╣╦╩╬▌▐█▏▕╭╮╯╰")

# A bullet/numbered list item lead: "- ", "* ", "1. ", "2) ", …
_LIST_LEAD_RE = re.compile(r"^\s*([-*]\s|\d+[.)]\s)")

# A code fence: a line whose stripped form opens/closes with ``` (optionally a lang).
_FENCE_RE = re.compile(r"^\s*```")

# An internal multi-space gap: a non-space, then 2+ spaces, then content. The
# offset where that *trailing* content resumes is the column start — aligned
# tables share it across rows even though first-column widths (and thus gap
# starts) vary.
_GAP_RE = re.compile(r"\S {2,}(?=\S)")


def _gap_offsets(line: str) -> set[int]:
    """Column-start offsets (where content resumes after an internal 2+-space gap)."""
    return {m.end() for m in _GAP_RE.finditer(line)}


def _is_columnar(lines: list[str]) -> bool:
    """Any line carries an internal 2+-space gap → intentional column alignment.

    Soft-wrapped prose (post-dedent) is single-spaced, so an internal run of 2+
    spaces is a strong table/alignment signal.  The test is deliberately
    *line-local* and monotonic: once a block contains a gapped line it stays
    preformatted as more lines stream in, so a block never flips prose→pre
    mid-stream in a way that would break content-key dedup (single-line blocks
    render identically either way, so labelling them ``pre`` is harmless)."""
    return any(_gap_offsets(line) for line in lines)


def _is_indented(lines: list[str]) -> bool:
    """Every non-blank line carries a 2+-space (or tab) leading indent."""
    body = [line for line in lines if line.strip()]
    return bool(body) and all(line[:2] == "  " or line[:1] == "\t" for line in body)


def _has_box(lines: list[str], box_chars: frozenset[str]) -> bool:
    return any(any(ch in box_chars for ch in line) for line in lines)


def classify_block(
    lines: list[str],
    *,
    preserve_prefixes: tuple[str, ...] = (),
    box_chars: frozenset[str] = _DEFAULT_BOX_CHARS,
) -> str:
    """Label a blank-line-separated block as prose / pre / list.

    ``code`` is assigned upstream by fence detection.  A block is preserved
    (``pre``/``list``) when it carries list/numbered leads, grammar-declared
    preserve glyphs, box-drawing, uniform indent, or column alignment; otherwise
    it is confident prose and safe to de-wrap.  Bias is toward *preserve*:
    over-preserving leaves original formatting intact, the lesser evil.
    """
    body = [line for line in lines if line.strip()]
    if any(_LIST_LEAD_RE.match(line) for line in body):
        return "list"
    if (
        (bool(preserve_prefixes) and any(line.lstrip().startswith(preserve_prefixes) for line in body))
        or _has_box(lines, box_chars)
        or _is_indented(lines)
        or _is_columnar(body)
    ):
        return "pre"
    return "prose"


def _split_blocks(
    lines: list[str],
    *,
    preserve_prefixes: tuple[str, ...],
    box_chars: frozenset[str],
) -> list[tuple[str, list[str]]]:
    """Split into labelled blocks: fenced code spans verbatim (inner blanks kept),
    the remainder blank-line-separated and classified."""
    blocks: list[tuple[str, list[str]]] = []
    current: list[str] = []

    def flush() -> None:
        if current:
            kind = classify_block(
                current, preserve_prefixes=preserve_prefixes, box_chars=box_chars
            )
            blocks.append((kind, list(current)))
            current.clear()

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if _FENCE_RE.match(line):
            flush()
            fence = [line]
            i += 1
            while i < n:
                fence.append(lines[i])
                if _FENCE_RE.match(lines[i]):
                    i += 1
                    break
                i += 1
            blocks.append(("code", fence))
            continue
        if not line.strip():
            flush()
            i += 1
            continue
        current.append(line)
        i += 1
    flush()
    return blocks


def reflow_paragraphs(
    lines: list[str],
    *,
    dedent: Callable[[str], str],
    preserve_prefixes: tuple[str, ...],
    preserve_strip: bool = False,
    post: Callable[[str], str] = lambda text: text,
    box_chars: frozenset[str] = _DEFAULT_BOX_CHARS,
) -> str:
    """De-wrap confident prose; preserve code / tables / lists / trees verbatim.

    Emits a *faithful multi-line* string: only ``prose`` blocks collapse their
    soft wraps (``" ".join``); ``code``/``pre``/``list`` render verbatim
    (``"\\n".join``, internal spaces preserved) so column alignment and
    indentation survive end to end.  Classification lives here because only the
    parser knows the source wrap width.  ``preserve_strip`` is retained for
    signature compatibility but no longer strips verbatim lines (the grammar's
    ``dedent`` owns leading-indent policy).
    """
    cleaned = [dedent(line) for line in lines]
    while cleaned and not cleaned[0].strip():
        cleaned.pop(0)
    while cleaned and not cleaned[-1].strip():
        cleaned.pop()

    rendered: list[str] = []
    for kind, block in _split_blocks(
        cleaned, preserve_prefixes=preserve_prefixes, box_chars=box_chars
    ):
        if kind == "prose":
            rendered.append(" ".join(line.strip() for line in block))
        else:
            rendered.append("\n".join(block))
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


def _merge_span(a: SpannedSegment, b: SpannedSegment) -> tuple[int, int, int]:
    """Span covering both inputs. Pinned (empty) spans are ignored so a real
    backing range is never lost to a pinned neighbour; epoch follows the later
    (larger-coordinate) contributor."""
    reals = [s for s in (a, b) if not s.pinned]
    if not reals:
        return a.start, a.end, max(a.epoch, b.epoch)
    start = min(s.start for s in reals)
    end = max(s.end for s in reals)
    epoch = max(s.epoch for s in reals)
    return start, end, epoch


def dedupe_adjacent_spanned(spanned: list[SpannedSegment]) -> list[SpannedSegment]:
    """Span-carrying twin of :func:`_dedupe_adjacent`.

    Identical collapse rules; when two renders of one logical block merge, the
    survivor's span is widened to cover both so commitment sees the full backing
    range. Segment payloads are reconciled by reusing ``_dedupe_adjacent`` on the
    two-element window, keeping a single source of truth for the merge policy.
    """
    result: list[SpannedSegment] = []
    for item in spanned:
        if not result:
            result.append(item)
            continue
        prev = result[-1]
        collapsed = _dedupe_adjacent([prev.segment, item.segment])
        if len(collapsed) == 1:
            start, end, epoch = _merge_span(prev, item)
            result[-1] = SpannedSegment(collapsed[0], start, end, epoch)
        else:
            result.append(item)
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
