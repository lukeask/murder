"""Codex harness grammar plugin."""

from __future__ import annotations

import re

from murder.llm.harnesses.transcripts.segments import PlanItem, Segment, SpannedSegment
from murder.llm.harnesses.transcripts._shared import (
    dedupe_adjacent_spanned,
    truncate_title,
    normalize_codex_text,
    reflow_paragraphs,
)
from murder.llm.harnesses.transcripts.toolkit import (
    BASE_CHROME_RULES,
    attribute_completion,
    chrome_matcher,
    record_dropped_completion,
    regex_match_rule,
    stripped_startswith_rule,
    stripped_substring_rule,
    substring_rule,
)

# ---- codex regexes --------------------------------------------------------- #
_CODEX_PROMPT_RE = re.compile(r"^\s*›\s*(.*)$")
_CODEX_BULLET_RE = re.compile(r"^•\s+(.*)$")
_CODEX_COMPLETION_RE = re.compile(r"^\s*─\s*Worked\s+for\s+(.+?)\s*─\s*$")
_CODEX_PLAN_ITEM_RE = re.compile(r"^\s*([✔□])\s+(.*)$")
_CODEX_RESULT_RE = re.compile(r"^\s*└\s?(.*)$")
_CODEX_ELIDED_RE = re.compile(r"…\s*\+\d+\s+lines")
_CODEX_TOOL_VERBS = (
    "Explored",
    "Ran ",
    "Edited ",
    "Added ",
    "Read ",
    "Searched ",
    "Waited ",
    "Called ",
    "Calling ",
)
# Codex echoes an unsent-input placeholder that must never become a user segment.
_CODEX_PLACEHOLDER_RE = re.compile(r"^Find and fix a bug in @filename$")
# codex 0.139's "update available" menu renders numbered options, the pointed
# one with the SAME `›` glyph as the real input prompt. A menu option is
# `[›|>] <digit>. <text>` — start-anchored digit+dot+space distinguishes it from
# a real (prose) user turn, which never opens with that shape.
_CODEX_UPDATE_MENU_OPTION_RE = re.compile(r"^\s*[›>]?\s*\d+\.\s+\S")
# /status modal: limit rows with bracketed bars and reset suffixes. Codex's
# parser skips non-• lines outright, but rows can still land inside a • block
# when projection races the overlay.
_CODEX_LIMIT_ROW_RE = re.compile(
    r"^\s*[│╭╰]?\s*"
    r"(?P<label>[A-Za-z0-9][\w/.\- ]*?)\s+limits?\s*:?\s*"
    r"(?:\[[^\]]*\]\s*)?"
    r"\d+(?:\.\d+)?\s*%\s*(?:left|remaining|used)?"
    r"(?:\s*\([^)]*resets?[^)]*\))?",
    re.IGNORECASE,
)
_CODEX_STATUS_SESSION_RE = re.compile(
    r"^\s*[│╭╰]?\s*Session:\s+[0-9a-f]{8}-[0-9a-f]{4}-",
    re.IGNORECASE,
)
_CODEX_STATUS_SLASH_RE = re.compile(r"^\s*/status\s*$", re.IGNORECASE)


# Codex chrome: shared base plus codex's status bars, box-drawing frame glyphs,
# the model-id footer (``gpt-…``) and the (submitted-or-live) ``›`` prompt line.
_codex_is_chrome = chrome_matcher(
    *BASE_CHROME_RULES,
    stripped_startswith_rule("gpt-", "tokens", "Tip:", "╭", "│", "╰", "■"),
    substring_rule("esc to interrupt", "background terminals running", "/ps to view"),
    stripped_substring_rule("ctrl + t to view transcript", "ctrl+t to view transcript"),
    stripped_substring_rule("Update available!", "Press enter to continue"),
    regex_match_rule(_CODEX_LIMIT_ROW_RE),
    regex_match_rule(_CODEX_STATUS_SESSION_RE),
    regex_match_rule(_CODEX_STATUS_SLASH_RE),
    regex_match_rule(_CODEX_PROMPT_RE),
    # NB: deliberately NOT a chrome rule for `_CODEX_UPDATE_MENU_OPTION_RE` — that
    # matcher also governs assistant prose body collection, and real codex
    # assistant turns contain numbered lists ("1. foo"); suppressing them as
    # chrome would silently drop list items. The menu's numbered options are
    # blocked at the user-segment emission site in parse_spanned instead.
)


def _codex_starts_block(lines: list[str], index: int) -> bool:
    line = lines[index]
    return bool(
        _CODEX_BULLET_RE.match(line)
        or _CODEX_COMPLETION_RE.match(line)
        or _CODEX_PROMPT_RE.match(line)
    )


def _dedent_codex(line: str) -> str:
    # Codex gutter-indents wrapped continuation lines by 2 spaces; strip that
    # gutter so soft-wrapped prose classifies as prose (and de-wraps) rather than
    # reading as a uniformly-indented preformatted block.
    if line.startswith("  "):
        return line[2:].rstrip()
    return line.rstrip()


def _reflow_codex_prose(lines: list[str]) -> str:
    return reflow_paragraphs(
        lines,
        dedent=_dedent_codex,
        preserve_prefixes=("- ", "└", "│", "├", "┌", "┘", "✔", "□"),
        post=normalize_codex_text,
    )


def _is_codex_live_prompt(lines: list[str], index: int) -> bool:
    """True when a `›` line is the live input box, not a submitted user turn."""
    for j in range(index + 1, len(lines)):
        line = lines[j]
        if _CODEX_BULLET_RE.match(line) or _CODEX_COMPLETION_RE.match(line):
            return False
        prompt = _CODEX_PROMPT_RE.match(line)
        if prompt and prompt.group(1).strip():
            return False
    return True


def _strip_codex_gutter(text: str) -> str:
    if text.startswith("│"):
        return text[1:].strip()
    return text


def _parse_codex_plan(lines: list[str], i: int) -> tuple[Segment | None, int]:
    title: str | None = None
    items: list[PlanItem] = []
    while i < len(lines) and not _codex_starts_block(lines, i):
        m = _CODEX_RESULT_RE.match(lines[i])
        item = _CODEX_PLAN_ITEM_RE.match(lines[i])
        if item:
            items.append(
                {
                    "done": item.group(1) == "✔",
                    "text": normalize_codex_text(item.group(2).strip()),
                }
            )
        elif m and m.group(1).strip():
            title = normalize_codex_text(m.group(1).strip())
        i += 1
    if title is None or not items:
        return None, i
    return {"type": "plan_update", "title": title, "items": items}, i


def _parse_codex_tool(label: str, lines: list[str], i: int) -> tuple[Segment, int]:
    result_lines: list[str] = []
    elided = False
    while i < len(lines) and not _codex_starts_block(lines, i):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        if _CODEX_ELIDED_RE.search(stripped) or "lines (ctrl" in stripped:
            elided = True
            i += 1
            continue
        m = _CODEX_RESULT_RE.match(line)
        if m:
            rest = m.group(1).strip()
            if rest:
                result_lines.append(normalize_codex_text(_strip_codex_gutter(rest)))
        elif line.startswith("    ") or stripped.startswith(("│", "✔", "⎿")):
            result_lines.append(normalize_codex_text(_strip_codex_gutter(stripped)))
        i += 1

    title = normalize_codex_text(label).strip()
    for prefix in ("Calling ", "Called ", "Ran "):
        if title.startswith(prefix):
            title = title[len(prefix):]
            break

    result = "\n".join(result_lines).strip() or None
    if title.startswith("Edited "):
        result = None
    return {
        "type": "tool_call",
        "title": truncate_title(title),
        "input": None,
        "result": result,
        "elided": elided,
        "running": False,
    }, i


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
    spanned: list[SpannedSegment] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        block_start = i

        prompt = _CODEX_PROMPT_RE.match(line)
        if prompt:
            text = prompt.group(1).strip()
            if (
                text
                and not _CODEX_PLACEHOLDER_RE.match(text)
                # An update-menu option (`› 1. Update now …`) is chrome, not a
                # user turn; its captured text begins with `<digit>. `.
                and not _CODEX_UPDATE_MENU_OPTION_RE.match(text)
                and not _is_codex_live_prompt(lines, i)
            ):
                spanned.append(
                    SpannedSegment(
                        {"type": "user", "text": normalize_codex_text(text)},
                        block_start,
                        i + 1,
                    )
                )
            i += 1
            continue

        completion = _CODEX_COMPLETION_RE.match(line)
        if completion:
            attribute_completion(
                spanned, completion.group(1), i + 1, on_drop=record_dropped_completion
            )
            i += 1
            continue

        bullet = _CODEX_BULLET_RE.match(line)
        if not bullet:
            i += 1
            continue

        label = bullet.group(1).strip()
        if (
            label.startswith("Working")
            or label.startswith("Starting MCP")
            or "esc to interrupt" in label
        ):
            i += 1
            continue
        if label == "Updated Plan":
            segment, i = _parse_codex_plan(lines, i + 1)
            if segment is not None:
                spanned.append(SpannedSegment(segment, block_start, i))
            continue
        if label.startswith(_CODEX_TOOL_VERBS):
            segment, i = _parse_codex_tool(label, lines, i + 1)
            spanned.append(SpannedSegment(segment, block_start, i))
            continue

        body = [label]
        i += 1
        while i < len(lines) and not _codex_starts_block(lines, i):
            if not _codex_is_chrome(lines[i]):
                body.append(lines[i])
            elif not lines[i].strip():
                body.append("")
            i += 1
        text = _reflow_codex_prose(body)
        if text:
            spanned.append(
                SpannedSegment(
                    {
                        "type": "assistant",
                        "phase": "intermediate",
                        "text": text,
                        "elapsed": None,
                    },
                    block_start,
                    i,
                )
            )
    return dedupe_adjacent_spanned(spanned)


def is_idle(pane_text: str) -> bool:
    """True when the Codex pane is awaiting input."""
    from murder.llm.harnesses.codex import CodexAdapter  # noqa: PLC0415

    return CodexAdapter().is_idle(pane_text)


def detect_live_choice_prompt(frame: str) -> None:  # type: ignore[return]
    """Codex has no choice prompt UI."""
    return None


def close_last_turn(segments: list[Segment]) -> None:
    """At idle, the last codex assistant block is the turn's final answer."""
    for segment in reversed(segments):
        if segment["type"] == "assistant":
            segment["phase"] = "final"
            return
        if segment["type"] == "user":
            return
