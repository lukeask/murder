"""Transcript accumulator and parse pipeline core.

Harness-agnostic; all harness-specific logic is in the grammar plugins.
This module imports NO harness adapter, breaking the parser↔adapter cycle.

Pipeline (one shape for all harnesses):

    feed(frame) -> _PaneScrollback reconciles successive fixed-height captures
    into one growing list of logical lines -> the per-harness grammar parses
    those lines top-to-bottom into segments, in pane order -> the accumulator
    keeps the longest parse seen (committed history is monotonic, never
    reordered, never merged across non-adjacent turns).
"""

from __future__ import annotations

import copy
from collections.abc import Iterable
from typing import Any

from murder.llm.harnesses.parsing import strip_ansi
from murder.llm.harnesses.transcripts._shared import (
    _RULE_RE,
    _dedupe_adjacent,
    _segment_key,
)
from murder.llm.harnesses.transcripts.registry import get_grammar, supports_harness
from murder.llm.harnesses.transcripts.segments import Segment


# Re-export public symbols for callers importing from this module directly.
__all__ = [
    "TranscriptAccumulator",
    "parse_frames",
    "_merge_segments",
    "_dedupe_adjacent",
    "_segment_key",
    "_strip_leading_system_prompt",
    "_PaneScrollback",
]


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


def _merge_segments(committed: list[Segment], parsed: list[Segment]) -> list[Segment]:
    """Carry scrolled-off segments forward in front of the freshly-parsed window."""
    if not committed:
        return [copy.deepcopy(s) for s in parsed]
    if not parsed:
        return committed

    keys_committed = [_segment_key(s) for s in committed]
    keys_parsed = [_segment_key(s) for s in parsed]

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
            merged.append(copy.deepcopy(parsed[j]))
            i += 1
            j += 1
        elif lcs[i + 1][j] >= lcs[i][j + 1]:
            merged.append(copy.deepcopy(committed[i]))
            i += 1
        else:
            merged.append(copy.deepcopy(parsed[j]))
            j += 1
    merged.extend(copy.deepcopy(s) for s in committed[i:])
    merged.extend(copy.deepcopy(s) for s in parsed[j:])
    return merged


def _resolve_choice_prompt(
    segments: list[Segment], prompt: Any, segment_key_fn: Any, choice_segment_fn: Any
) -> list[Segment]:
    target_key = segment_key_fn(choice_segment_fn(prompt))
    for index in range(len(segments) - 1, -1, -1):
        segment = segments[index]
        if segment["type"] != "choice_prompt" or segment.get("answered"):
            continue
        if segment_key_fn(segment) != target_key:
            continue
        resolved = copy.deepcopy(segment)
        resolved["answered"] = True
        resolved["chosen"] = prompt.selected_option.number
        segments[index] = resolved
        break
    # The choice dialog question is already stored in choice_prompt.question; remove
    # any assistant segment with the identical text so it doesn't appear twice.
    question = (prompt.question or "").strip()
    if question:
        segments = [
            s for s in segments
            if not (s.get("type") == "assistant" and s.get("text", "").strip() == question)
        ]
    return segments


def _strip_leading_system_prompt(
    blocks: list[list[str]], system_prompt: str | None
) -> list[list[str]]:
    """Re-export of the pure helper in _shared for backward compatibility."""
    from murder.llm.harnesses.transcripts._shared import strip_leading_system_prompt  # noqa: PLC0415

    return strip_leading_system_prompt(blocks, system_prompt)


class TranscriptAccumulator:
    """Append pane captures and expose the accumulated typed transcript."""

    def __init__(self, harness: str, *, system_prompt: str | None = None) -> None:
        self.harness = harness
        self.system_prompt = system_prompt
        # Ground-truth user turns (recorded at the send boundary), used by
        # markerless grammars to recognise echoed user content. Updated by the
        # producer before each feed; empty is fine.
        self.user_texts: list[str] = []
        self._scrollback = _PaneScrollback()
        self._state = "working"
        self._segments: list[Segment] = []
        self._active_choice_prompt: Any = None

    def feed(self, frame: str) -> None:
        grammar = get_grammar(self.harness) if supports_harness(self.harness) else None

        # Per-harness frame preprocessing (e.g. cursor tags colour-coded user
        # lines) runs on the raw capture before scrollback reconciliation.
        if grammar is not None and hasattr(grammar, "preprocess_frame"):
            frame = grammar.preprocess_frame(frame)
        self._scrollback.feed(frame)

        live_choice_prompt = None
        if grammar is not None:
            live_choice_prompt = grammar.detect_live_choice_prompt(frame)

        # Determine state.
        if live_choice_prompt is not None:
            self._state = "awaiting_approval"
        elif grammar is not None and grammar.is_idle(frame):
            self._state = "awaiting_input"
        else:
            self._state = "working"

        # Parse current scrollback.
        if grammar is not None:
            parsed = grammar.parse_lines(
                self._scrollback.lines, self.system_prompt, self.user_texts
            )
        else:
            parsed = []

        if live_choice_prompt is not None:
            from murder.llm.harnesses.transcripts.grammar.claude_code import (  # noqa: PLC0415
                choice_prompt_segment,
            )
            # The choice question text was emitted by the assistant and then the
            # dialog was rendered. Parse_lines may capture it as an assistant
            # segment; remove it so it doesn't duplicate choice_prompt.question.
            question = (live_choice_prompt.question or "").strip()
            if question:
                parsed = [
                    s for s in parsed
                    if not (s.get("type") == "assistant" and s.get("text", "").strip() == question)
                ]
            parsed.append(choice_prompt_segment(live_choice_prompt))

        self._segments = _dedupe_adjacent(_merge_segments(self._segments, parsed))

        if live_choice_prompt is not None:
            self._active_choice_prompt = live_choice_prompt
        elif self._active_choice_prompt is not None:
            from murder.llm.harnesses.transcripts.grammar.claude_code import (  # noqa: PLC0415
                choice_prompt_segment,
            )
            self._segments = _resolve_choice_prompt(
                self._segments,
                self._active_choice_prompt,
                _segment_key,
                choice_prompt_segment,
            )
            self._active_choice_prompt = None

    def to_dict(self) -> dict[str, Any]:
        segments = copy.deepcopy(self._segments)
        if self._state == "awaiting_input" and supports_harness(self.harness):
            grammar = get_grammar(self.harness)
            grammar.close_last_turn(segments)
        return {
            "harness": self.harness,
            "state": self._state,
            "condensed": None,
            "segments": segments,
        }


def parse_frames(
    harness: str,
    frames: Iterable[str],
    *,
    system_prompt: str | None = None,
    user_texts: list[str] | None = None,
) -> dict[str, Any]:
    acc = TranscriptAccumulator(harness, system_prompt=system_prompt)
    if user_texts is not None:
        acc.user_texts = user_texts
    for frame in frames:
        acc.feed(frame)
    return acc.to_dict()
