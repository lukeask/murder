"""Typed, stateful transcript parsing for terminal harness panes.

The parser is a pure function of *pane shape*. It knows the syntactic chrome of
each harness — prompt markers, tool-call headers, completion markers, plan
blocks, agent-event lines — and derives typed segments from that structure
alone. It has no knowledge of any specific command, file name, SQL query, or
prose content of any session; a different CC or codex session parses through the
exact same code path.

Pipeline (one shape for both harnesses):

    feed(frame) -> _PaneScrollback reconciles successive fixed-height captures
    into one growing list of logical lines -> the per-harness grammar parses
    those lines top-to-bottom into segments, in pane order -> the accumulator
    keeps the longest parse seen (committed history is monotonic, never
    reordered, never merged across non-adjacent turns).
"""

from __future__ import annotations

import copy
import re
from collections.abc import Callable, Iterable
from typing import Any, Literal, TypedDict

from murder.llm.harnesses.choice_prompt import (
    MultipleChoicePrompt,
    parse_claude_code_choice_prompt,
)
from murder.llm.harnesses.parsing import strip_ansi


# --------------------------------------------------------------------------- #
# Typed segment schema (mirrors tests/fixtures/transcripts/SCHEMA.md).
#
# Segments are a discriminated union keyed on `type`. They are plain dicts at
# runtime (built inline by the grammars, persisted via json), but the per-variant
# TypedDicts let mypy catch a wrong/missing key — a class of bug that would
# otherwise silently drop a segment from the LCS merge with no error.
# --------------------------------------------------------------------------- #
class UserSegment(TypedDict):
    type: Literal["user"]
    text: str


class AssistantSegment(TypedDict):
    type: Literal["assistant"]
    phase: Literal["intermediate", "final"]
    text: str
    elapsed: str | None


class ToolCallSegment(TypedDict):
    type: Literal["tool_call"]
    title: str
    input: str | None
    result: str | None
    elided: bool
    running: bool


class PlanItem(TypedDict):
    done: bool
    text: str


class PlanUpdateSegment(TypedDict):
    type: Literal["plan_update"]
    title: str
    items: list[PlanItem]


class AgentEventSegment(TypedDict):
    type: Literal["agent_event"]
    name: str
    status: Literal["dispatched", "completed"]
    elapsed: str | None


class ChoiceOptionDict(TypedDict):
    number: int
    label: str
    description: str | None


class ChoicePromptSegment(TypedDict):
    type: Literal["choice_prompt"]
    question: str
    options: list[ChoiceOptionDict]
    footer: str | None
    selected: int
    answered: bool
    chosen: int | None


Segment = (
    UserSegment
    | AssistantSegment
    | ToolCallSegment
    | PlanUpdateSegment
    | AgentEventSegment
    | ChoicePromptSegment
)

# Canonical list of segment `type` discriminants. Every projection of a
# TranscriptDoc (persistence turns, TUI render, summary payload) must account for
# each of these; a type seen at runtime that is NOT here means the grammar grew a
# variant a projection forgot, and the projection logs rather than silently
# dropping it.
SEGMENT_TYPES: tuple[str, ...] = (
    "user",
    "assistant",
    "tool_call",
    "plan_update",
    "agent_event",
    "choice_prompt",
)

_TITLE_MAX = 160

# ---- shared chrome ---------------------------------------------------------- #
_RULE_RE = re.compile(r"^\s*[─━═]{8,}\s*$")

# ---- claude_code grammar ---------------------------------------------------- #
_CC_PROMPT_RE = re.compile(r"^\s*❯[\s ]*(.*)$")
_CC_CHOICE_OPTION_PROMPT_RE = re.compile(r"^\s*❯[\s\xa0]*\d+\.\s+")
_CC_BULLET_RE = re.compile(r"^●\s+(.*)$")
_CC_COMPLETION_RE = re.compile(
    r"^\s*[✻✶✳✽✢]\s+(?:Worked|Baked|Churned|Sautéed|Cooked|Brewed|Noodled|Cogitated)\s+for\s+(\d.+?)\s*$"
)
_CC_AGENT_DONE_RE = re.compile(r'^●\s+Agent\s+"(.+?)"\s+completed\s+·\s+(.+?)\s*$')
_CC_AGENT_START_RE = re.compile(r"^●\s+Agent\((.+?)\)\s*$")
# A tool header: `● Bash(...)`, `● Read(...)`, etc. The verb is a single
# capitalized word immediately followed by `(`.
_CC_TOOL_RE = re.compile(r"^●\s+([A-Z][a-zA-Z]+)\((.*)$")
# A collapsed search/read/edit summary line (not a `●` bullet): the pane shows
# `  Searched for 1 pattern (ctrl+o to expand)` once a tool finished.
_CC_SUMMARY_RE = re.compile(
    r"^\s+(Searched|Searching|Read|Reading|Wrote|Writing|Edited|Editing|Listed|Listing"
    r"|Found|Fetched|Fetching)\b.*"
)
# An in-flight tool progress line (`● Searching for 1 pattern…`,
# `● Listing 1 directory…`) — the trailing ellipsis marks it not-yet-committed.
# Its committed form (`Searched for …`) lands separately, so the running form is
# transient chrome.
_CC_RUNNING_SUMMARY_RE = re.compile(
    r"^(Searching|Searched|Reading|Read|Writing|Wrote|Editing|Edited|Listing|Listed"
    r"|Finding|Found|Fetching|Fetched)\b.*…"
)
# Spinner / progress line: `✻ Finagling… (7s · ↓ 305 tokens)` or `· Working…`.
_CC_SPINNER_RE = re.compile(
    r"^\s*[·*✻✶✳✽✢⠁-⣿◐◓◑◒]?\s*[A-Z]\w+…+\s*\([^)]*(?:tokens|thought|↑|↓|esc to)"
)
_CC_AGENT_ROSTER_RE = re.compile(r"^\s*[●◯]\s+(?:main|general-purpose)\b")
_CC_UNCACHED_NOTICE_RE = re.compile(
    r"(?:~?\d[\d.,]*(?:\s*[kKmM])?(?:\s+tokens)?)\s+uncached\b"
    r"(?:\s+·\s+/clear to start fresh)?",
    re.IGNORECASE,
)
_CC_RESULT_RE = re.compile(r"^\s*⎿\s?(.*)$")
_CC_ELIDED_RE = re.compile(r"…\s*\+\d+\s+lines")

# ---- codex grammar ---------------------------------------------------------- #
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
# Codex echoes an unsent-input placeholder at the bottom of the pane that must
# never become a user segment. Matched against the extracted prompt text (the
# `›` marker already stripped).
_CODEX_PLACEHOLDER_RE = re.compile(r"^Find and fix a bug in @filename$")


def _truncate_title(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > _TITLE_MAX:
        text = text[: _TITLE_MAX - 1].rstrip() + "…"
    return text


def _normalize_codex_text(text: str) -> str:
    return (
        text.replace("’", "'")
        .replace("‘", "'")
        .replace("“", '"')
        .replace("”", '"')
    )


def _state_adapter(harness: str) -> Any | None:
    if harness == "claude_code":
        from murder.llm.harnesses.claude_code import ClaudeCodeAdapter

        return ClaudeCodeAdapter()
    if harness == "codex":
        from murder.llm.harnesses.codex import CodexAdapter

        return CodexAdapter()
    if harness == "cursor":
        from murder.llm.harnesses.cursor import CursorAdapter

        return CursorAdapter()
    return None


# --------------------------------------------------------------------------- #
# Scrollback reconciliation.
# --------------------------------------------------------------------------- #
def _line_weight(line: str) -> float:
    stripped = line.strip()
    if not stripped:
        return 0.05
    if _RULE_RE.match(line):
        return 0.1
    if "bypass permissions" in line or "esc to interrupt" in line:
        return 0.25
    return min(8.0, 1.0 + len(stripped) / 24)


class _PaneScrollback:
    """Reconcile successive fixed-height pane captures into logical scrollback."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self._previous: list[str] = []
        self._start = 0

    def feed(self, pane_text: str) -> None:
        new = strip_ansi(pane_text).splitlines()
        if not self._previous:
            self.lines = list(new)
            self._previous = new
            return

        # d = how far the pane's top moved forward through scrollback.
        best_d = 0
        best_score = -1.0
        limit = max(len(self._previous), len(new))
        for d in range(0, limit + 1):
            score = 0.0
            compared = 0
            for j, line in enumerate(new):
                i = j + d
                if i >= len(self._previous):
                    break
                compared += 1
                if line == self._previous[i]:
                    score += _line_weight(line)
            score -= d * 0.01
            if compared and score > best_score:
                best_score = score
                best_d = d

        self._start += best_d
        end = self._start + len(new)
        if end > len(self.lines):
            self.lines.extend([""] * (end - len(self.lines)))
        self.lines[self._start:end] = new
        self._previous = new


# --------------------------------------------------------------------------- #
# claude_code parser.
# --------------------------------------------------------------------------- #
def _is_live_prompt(lines: list[str], index: int) -> bool:
    """A `❯` line is the live input box when it sits between two horizontal
    rules (the bottom input frame), not committed scrollback."""
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


def _cc_is_chrome(line: str) -> bool:
    stripped = line.strip()
    return bool(
        not stripped
        or _RULE_RE.match(line)
        or _CC_SPINNER_RE.match(line)
        or _CC_AGENT_ROSTER_RE.match(line)
        or "bypass permissions" in line
        or "esc to interrupt" in line
        or "shift+tab to cycle" in line
        or _CC_UNCACHED_NOTICE_RE.search(line)
        or "/clear to start fresh" in line
        or "↑/↓ to select" in line
        or "to manage" in line
        or "Backgrounded agent" in stripped
        or stripped.startswith("Tip:")
        or (stripped.startswith("⎿") and "Tip:" in stripped)
        or stripped.startswith(("▐", "▝", "▘", "▛", "▜"))
        or "Claude Code v" in line
        or "Waiting for" in stripped
    )


def _cc_starts_block(lines: list[str], index: int) -> bool:
    line = lines[index]
    return bool(
        (_CC_PROMPT_RE.match(line) and not _is_live_prompt(lines, index))
        or _CC_BULLET_RE.match(line)
        or _CC_COMPLETION_RE.match(line)
        or _CC_SUMMARY_RE.match(line)
    )


def _strip_expand_hint(text: str) -> str:
    return re.sub(r"\s*\(ctrl\+[ot][^)]*\)\s*$", "", text).rstrip()


def _dedent_cc(line: str) -> str:
    if line.startswith("  "):
        return line[2:].rstrip()
    return line.rstrip()


def _reflow_paragraphs(
    lines: list[str],
    *,
    dedent: Callable[[str], str],
    preserve_prefixes: tuple[str, ...],
    preserve_strip: bool,
    post: Callable[[str], str] = lambda text: text,
) -> str:
    """De-wrap prose into paragraphs; preserve tables / lists / diffs verbatim.

    Shared by the CC and codex grammars — only the per-line ``dedent``, the set
    of ``preserve_prefixes`` that mark a structural (non-reflowed) paragraph,
    whether preserved paragraphs are stripped per line, and the ``post`` pass
    differ between harnesses.
    """
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
            line.lstrip().startswith(preserve_prefixes)
            or re.match(r"^\s*\d+\.\s", line)
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


def _reflow_prose(lines: list[str]) -> str:
    """De-wrap CC prose into paragraphs; preserve tables / lists / diffs verbatim."""
    return _reflow_paragraphs(
        lines,
        dedent=_dedent_cc,
        preserve_prefixes=("┌", "│", "├", "└", "┘", "- ", "* "),
        preserve_strip=False,
    )


def _reflow_user(lines: list[str]) -> str:
    cleaned = [_dedent_cc(line) for line in lines]
    return " ".join(line.strip() for line in cleaned if line.strip())


def _cc_collect_result(lines: list[str], i: int) -> tuple[str | None, bool, int]:
    """Collect a tool result from `⎿` continuation lines after a tool header."""
    result_lines: list[str] = []
    elided = False
    while i < len(lines):
        line = lines[i]
        if _cc_starts_block(lines, i):
            break
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        m = _CC_RESULT_RE.match(line)
        if m:
            body = m.group(1)
            if _CC_ELIDED_RE.search(body) or body.strip().startswith("…"):
                elided = True
            elif body.strip():
                result_lines.append(_strip_expand_hint(body).rstrip())
            i += 1
            continue
        # An indented continuation of the result block (wrapped output).
        if line.startswith("  "):
            if _CC_ELIDED_RE.search(stripped) or stripped.startswith("…"):
                elided = True
            else:
                result_lines.append(_strip_expand_hint(stripped))
            i += 1
            continue
        break
    result = "\n".join(result_lines).strip() or None
    return result, elided, i


def _parse_cc(lines: list[str]) -> list[Segment]:
    segments: list[Segment] = []
    i = 0
    while i < len(lines):
        line = lines[i]

        prompt = _CC_PROMPT_RE.match(line)
        if (
            prompt
            and prompt.group(1).strip()
            and not _is_live_prompt(lines, i)
            and not _CC_CHOICE_OPTION_PROMPT_RE.match(line)
        ):
            body = [prompt.group(1)]
            i += 1
            while i < len(lines) and lines[i].startswith("  ") and not _cc_is_chrome(lines[i]):
                if _cc_starts_block(lines, i):
                    break
                body.append(lines[i])
                i += 1
            segments.append({"type": "user", "text": _reflow_user(body)})
            continue

        done = _CC_AGENT_DONE_RE.match(line)
        if done:
            segments.append(
                {
                    "type": "agent_event",
                    "name": done.group(1),
                    "status": "completed",
                    "elapsed": done.group(2),
                }
            )
            i += 1
            continue

        start = _CC_AGENT_START_RE.match(line)
        if start:
            segments.append(
                {
                    "type": "agent_event",
                    "name": start.group(1),
                    "status": "dispatched",
                    "elapsed": None,
                }
            )
            i += 1
            # Consume the `⎿ Backgrounded agent…` line.
            while i < len(lines) and not _cc_starts_block(lines, i):
                i += 1
            continue

        completion = _CC_COMPLETION_RE.match(line)
        if completion:
            for segment in reversed(segments):
                if segment["type"] == "assistant":
                    segment["phase"] = "final"
                    segment["elapsed"] = completion.group(1)
                    break
            i += 1
            continue

        tool = _CC_TOOL_RE.match(line)
        if tool:
            verb = tool.group(1)
            body = tool.group(2)
            i += 1
            # Join wrapped continuation of the command itself (deeper indent,
            # not a `⎿` result line).
            while (
                i < len(lines)
                and not _cc_starts_block(lines, i)
                and not _CC_RESULT_RE.match(lines[i])
                and lines[i].strip()
                and not _cc_is_chrome(lines[i])
            ):
                body += " " + lines[i].strip()
                i += 1
            command = re.sub(r"\)\s*$", "", body)
            command = _strip_expand_hint(re.sub(r"\s+", " ", command).strip())
            result, elided, i = _cc_collect_result(lines, i)
            segments.append(
                {
                    "type": "tool_call",
                    "title": _truncate_title(command),
                    "input": _truncate_title(command) if verb == "Bash" else None,
                    "result": result,
                    "elided": elided or result is None,
                    "running": False,
                }
            )
            continue

        if _CC_SUMMARY_RE.match(line) and not _CC_BULLET_RE.match(line):
            if _CC_RUNNING_SUMMARY_RE.match(line.strip()):
                # In-flight progress form (`Searching for 1 pattern…`); skip — it
                # resolves to a committed `Searched for …` summary.
                i += 1
                _, _, i = _cc_collect_result(lines, i)
                continue
            title = _strip_expand_hint(line.strip())
            i += 1
            result, elided, i = _cc_collect_result(lines, i)
            segments.append(
                {
                    "type": "tool_call",
                    "title": _truncate_title(title),
                    "input": None,
                    "result": result,
                    "elided": elided or result is None,
                    "running": False,
                }
            )
            continue

        bullet = _CC_BULLET_RE.match(line)
        if bullet and _CC_RUNNING_SUMMARY_RE.match(bullet.group(1).strip()):
            # In-flight tool progress; skip it and any collapsed preview lines.
            i += 1
            _, _, i = _cc_collect_result(lines, i)
            continue

        if bullet:
            body = [bullet.group(1)]
            i += 1
            while i < len(lines) and not _cc_starts_block(lines, i):
                if not _cc_is_chrome(lines[i]):
                    body.append(lines[i])
                elif not lines[i].strip():
                    body.append("")
                i += 1
            text = _reflow_prose(body)
            if text:
                segments.append(
                    {
                        "type": "assistant",
                        "phase": "intermediate",
                        "text": text,
                        "elapsed": None,
                    }
                )
            continue

        i += 1
    return _dedupe_adjacent(segments)


def _choice_prompt_segment(prompt: MultipleChoicePrompt) -> ChoicePromptSegment:
    options: list[ChoiceOptionDict] = [
        {
            "number": option.number,
            "label": option.label,
            "description": option.description or None,
        }
        for option in prompt.options
    ]
    return {
        "type": "choice_prompt",
        "question": prompt.question,
        "options": options,
        "footer": prompt.footer or None,
        "selected": prompt.selected_option.number,
        "answered": False,
        "chosen": None,
    }


# --------------------------------------------------------------------------- #
# codex parser.
# --------------------------------------------------------------------------- #
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
    return _reflow_paragraphs(
        lines,
        dedent=lambda line: line.rstrip(),
        preserve_prefixes=("- ", "└", "│", "├", "┌", "┘", "✔", "□"),
        preserve_strip=True,
        post=_normalize_codex_text,
    )


def _is_codex_live_prompt(lines: list[str], index: int) -> bool:
    """True when a `›` line is the live input box, not a submitted user turn.

    A submitted prompt is always followed (eventually) by committed agent
    output — a `•` bullet, a completion marker, or another submitted `›`
    prompt. The live input box at the bottom of the pane has only chrome below
    it (the status bar `gpt-… · cwd`, blanks, tips) until end-of-pane. So a `›`
    line whose forward scan reaches end-of-pane without crossing committed
    content is the live input box and is suppressed.
    """
    for j in range(index + 1, len(lines)):
        line = lines[j]
        if _CODEX_BULLET_RE.match(line) or _CODEX_COMPLETION_RE.match(line):
            return False
        prompt = _CODEX_PROMPT_RE.match(line)
        if prompt and prompt.group(1).strip():
            return False
    return True


def _close_last_codex_turn(segments: list[Segment]) -> None:
    """Flip the transcript's last assistant block to `final` (elapsed unknown).

    Codex renders no `─ Worked for … ─` completion marker in the pane, so the
    closed turn is identified structurally: when the session is idle (the input
    placeholder is showing), the last assistant block of the accumulated
    transcript is that turn's final answer. elapsed stays None because the pane
    never showed a duration. Applied to the *committed* transcript once, not
    per-frame, so mid-session windows never mark a premature final.
    """
    for segment in reversed(segments):
        if segment["type"] == "assistant":
            segment["phase"] = "final"
            return
        if segment["type"] == "user":
            return


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
                    "text": _normalize_codex_text(item.group(2).strip()),
                }
            )
        elif m and m.group(1).strip():
            title = _normalize_codex_text(m.group(1).strip())
        i += 1
    if title is None or not items:
        return None, i
    return {"type": "plan_update", "title": title, "items": items}, i


def _strip_codex_gutter(text: str) -> str:
    """Strip codex's output-box gutter (`│ `) decoration from a result line."""
    if text.startswith("│"):
        return text[1:].strip()
    return text


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
                result_lines.append(_normalize_codex_text(_strip_codex_gutter(rest)))
        elif line.startswith("    ") or stripped.startswith(("│", "✔", "⎿")):
            result_lines.append(_normalize_codex_text(_strip_codex_gutter(stripped)))
        i += 1

    title = _normalize_codex_text(label).strip()
    for prefix in ("Calling ", "Called ", "Ran "):
        if title.startswith(prefix):
            title = title[len(prefix):]
            break

    result = "\n".join(result_lines).strip() or None
    # An `Edited file (+N −M)` header is self-describing; the body is the diff
    # preview, which the pane elides behind the header — keep title-only.
    if title.startswith("Edited "):
        result = None
    return {
        "type": "tool_call",
        "title": _truncate_title(title),
        "input": None,
        "result": result,
        "elided": elided,
        "running": False,
    }, i


def _parse_codex(lines: list[str]) -> list[Segment]:
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
                segments.append({"type": "user", "text": _normalize_codex_text(text)})
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


# --------------------------------------------------------------------------- #
# Reconciliation helpers shared by both harnesses.
# --------------------------------------------------------------------------- #
def _dedupe_adjacent(segments: list[Segment]) -> list[Segment]:
    """Collapse a block's streaming re-renders into one segment.

    Two adjacent segments can be two renders of one logical event:
    - A tool rendered first in a pending form (codex `• Calling X` with only an
      approval line) then a completed form (`• Called X` with the real result) —
      same title, keep the richer result.
    - An assistant block captured truncated in an early frame then grown in a
      later one — one text is a prefix of the other; keep the longer (final)
      form. This is the streaming-tail rule applied at the segment seam.
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
            # Keep the completed render: prefer the one that carries a result.
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
            # Keep the longer (grown) text; carry the stronger phase/elapsed.
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


def _is_streaming_extension(a: str, b: str) -> bool:
    """True when one text is a prefix of the other (a block that grew between
    frames). Distinct messages — even similar ones — do not prefix-match."""
    return a.startswith(b) or b.startswith(a)


def _segment_key(segment: Segment) -> tuple:
    """A stable identity for a segment that survives streaming growth.

    Prose text grows character-by-character and a block's phase/elapsed flip
    when its completion marker appears, so exact equality cannot align the same
    logical block across frames. The key captures only what is stable:
    user/assistant by a text prefix, tool by title, plan by its done-count,
    agent by name+status.
    """
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


def _merge_segments(committed: list[Segment], parsed: list[Segment]) -> list[Segment]:
    """Carry scrolled-off segments forward in front of the freshly-parsed window.

    `parsed` is the parse of the *whole currently-visible* scrollback — a clean,
    in-order window whose top may have scrolled past segments we committed in an
    earlier frame. We therefore keep the previously-committed segments that
    precede where `parsed` now begins, then take `parsed` verbatim as the live
    portion. Result: committed = (scrolled-off prefix) + (current window), in
    order, never reordered or merged across non-adjacent turns. The current
    window is authoritative for everything it still shows (streaming tail
    updates and phase flips land for free).
    """
    if not committed:
        return [copy.deepcopy(s) for s in parsed]
    if not parsed:
        return committed

    keys_committed = [_segment_key(s) for s in committed]
    keys_parsed = [_segment_key(s) for s in parsed]

    # Order-preserving sequence alignment (LCS by key). `parsed` is a window
    # over the session; its segments either already exist in committed (the
    # overlap — refresh those in place with the fresher text/phase) or are new
    # (insert them at their in-order position). Committed segments absent from
    # the window (scrolled off above or below) are kept untouched. Nothing is
    # ever reordered and the committed count is monotonic.
    n, m = len(committed), len(parsed)
    lcs = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            if keys_committed[i] == keys_parsed[j]:
                lcs[i][j] = 1 + lcs[i + 1][j + 1]
            else:
                lcs[i][j] = max(lcs[i + 1][j], lcs[i][j + 1])

    merged: list[Segment] = []
    i = j = 0
    while i < n and j < m:
        if keys_committed[i] == keys_parsed[j]:
            # Same logical segment: take the window's fresher form.
            merged.append(copy.deepcopy(parsed[j]))
            i += 1
            j += 1
        elif lcs[i + 1][j] >= lcs[i][j + 1]:
            # Committed-only segment (scrolled off the window): keep it.
            merged.append(copy.deepcopy(committed[i]))
            i += 1
        else:
            # Window-only segment (newly visible): insert it.
            merged.append(copy.deepcopy(parsed[j]))
            j += 1
    merged.extend(copy.deepcopy(s) for s in committed[i:])
    merged.extend(copy.deepcopy(s) for s in parsed[j:])
    return merged


def _resolve_choice_prompt(segments: list[Segment], prompt: MultipleChoicePrompt) -> list[Segment]:
    target_key = _segment_key(_choice_prompt_segment(prompt))
    for index in range(len(segments) - 1, -1, -1):
        segment = segments[index]
        if segment["type"] != "choice_prompt" or segment.get("answered"):
            continue
        if _segment_key(segment) != target_key:
            continue
        resolved = copy.deepcopy(segment)
        resolved["answered"] = True
        resolved["chosen"] = prompt.selected_option.number
        segments[index] = resolved
        break
    return segments


# --------------------------------------------------------------------------- #
# Accumulator.
# --------------------------------------------------------------------------- #
class TranscriptAccumulator:
    """Append pane captures and expose the accumulated typed transcript."""

    def __init__(self, harness: str) -> None:
        self.harness = harness
        self._scrollback = _PaneScrollback()
        self._state = "working"
        self._segments: list[Segment] = []
        self._active_choice_prompt: MultipleChoicePrompt | None = None

    def feed(self, frame: str) -> None:
        self._scrollback.feed(frame)
        live_choice_prompt = None
        if self.harness == "claude_code":
            live_choice_prompt = parse_claude_code_choice_prompt(strip_ansi(frame))
        self._state = _state_from_frame(self.harness, frame, live_choice_prompt)
        if self.harness == "claude_code":
            parsed = _parse_cc(self._scrollback.lines)
        elif self.harness == "codex":
            parsed = _parse_codex(self._scrollback.lines)
        else:
            parsed = []
        if live_choice_prompt is not None:
            parsed.append(_choice_prompt_segment(live_choice_prompt))
        # Append-only reconciliation: keep segments that have scrolled off the
        # top of the current window, update the live tail in place, and append
        # newly-visible segments. The final dedupe coalesces a block that was
        # captured truncated in one frame and grown in the next (the LCS merge
        # keys assistants on a text prefix, so a stub and its grown form survive
        # as two nodes until coalesced here).
        self._segments = _dedupe_adjacent(_merge_segments(self._segments, parsed))
        if live_choice_prompt is not None:
            self._active_choice_prompt = live_choice_prompt
        elif self._active_choice_prompt is not None:
            self._segments = _resolve_choice_prompt(self._segments, self._active_choice_prompt)
            self._active_choice_prompt = None

    def to_dict(self) -> dict[str, Any]:
        segments = copy.deepcopy(self._segments)
        # Codex shows no completion marker; when the session is idle its last
        # assistant block is the turn's final answer. Resolved at read time so a
        # mid-session window never marks a premature final.
        if self.harness == "codex" and self._state == "awaiting_input":
            _close_last_codex_turn(segments)
        return {
            "harness": self.harness,
            "state": self._state,
            # TODO: populated by transcript_summarize.summarize_doc once the
            # summarization pass is wired onto idle transitions; deterministic
            # parsing leaves it null (see SCHEMA.md).
            "condensed": None,
            "segments": segments,
        }


def _state_from_frame(
    harness: str,
    frame: str,
    live_choice_prompt: MultipleChoicePrompt | None = None,
) -> str:
    if live_choice_prompt is not None:
        return "awaiting_approval"
    adapter = _state_adapter(harness)
    if adapter is not None and adapter.is_idle(frame):
        return "awaiting_input"
    return "working"


def supports_harness(harness: str) -> bool:
    return harness in {"claude_code", "codex"}


def parse_frames(harness: str, frames: Iterable[str]) -> dict[str, Any]:
    acc = TranscriptAccumulator(harness)
    for frame in frames:
        acc.feed(frame)
    return acc.to_dict()


__all__ = ["SEGMENT_TYPES", "TranscriptAccumulator", "parse_frames", "supports_harness"]
