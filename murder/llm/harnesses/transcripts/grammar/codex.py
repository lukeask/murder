"""Codex harness grammar plugin."""

from __future__ import annotations

import re

from murder.llm.harnesses.transcripts.segments import PlanItem, Segment
from murder.llm.harnesses.transcripts._shared import (
    _RULE_RE,
    _dedupe_adjacent,
    truncate_title,
    normalize_codex_text,
    reflow_paragraphs,
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


def _codex_is_chrome(line: str) -> bool:
    stripped = line.strip()
    return bool(
        not stripped
        or _RULE_RE.match(line)
        or stripped.startswith("gpt-")
        or stripped.startswith("tokens")
        or "esc to interrupt" in line
        or "background terminals running" in line
        or "/ps to view" in line
        or "ctrl + t to view transcript" in stripped
        or "ctrl+t to view transcript" in stripped
        or stripped.startswith(("╭", "│", "╰", "■"))
        or stripped.startswith("Tip:")
        or _CODEX_PROMPT_RE.match(line)
    )


def _codex_starts_block(lines: list[str], index: int) -> bool:
    line = lines[index]
    return bool(
        _CODEX_BULLET_RE.match(line)
        or _CODEX_COMPLETION_RE.match(line)
        or _CODEX_PROMPT_RE.match(line)
    )


def _reflow_codex_prose(lines: list[str]) -> str:
    return reflow_paragraphs(
        lines,
        dedent=lambda line: line.rstrip(),
        preserve_prefixes=("- ", "└", "│", "├", "┌", "┘", "✔", "□"),
        preserve_strip=True,
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
    system_prompt: str | None = None,  # noqa: ARG001
    user_texts: list[str] | None = None,  # noqa: ARG001
) -> list[Segment]:
    segments: list[Segment] = []
    i = 0
    while i < len(lines):
        line = lines[i]

        prompt = _CODEX_PROMPT_RE.match(line)
        if prompt:
            text = prompt.group(1).strip()
            if (
                text
                and not _CODEX_PLACEHOLDER_RE.match(text)
                and not _is_codex_live_prompt(lines, i)
            ):
                segments.append({"type": "user", "text": normalize_codex_text(text)})
            i += 1
            continue

        completion = _CODEX_COMPLETION_RE.match(line)
        if completion:
            for segment in reversed(segments):
                if segment["type"] == "assistant":
                    segment["phase"] = "final"
                    segment["elapsed"] = completion.group(1)
                    break
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
                segments.append(segment)
            continue
        if label.startswith(_CODEX_TOOL_VERBS):
            segment, i = _parse_codex_tool(label, lines, i + 1)
            segments.append(segment)
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
            segments.append(
                {
                    "type": "assistant",
                    "phase": "intermediate",
                    "text": text,
                    "elapsed": None,
                }
            )
    return _dedupe_adjacent(segments)


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
