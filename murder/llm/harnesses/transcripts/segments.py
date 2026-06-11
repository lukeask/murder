"""Typed segment schema for transcript parsing.

Harness-agnostic, zero imports.  Every projection of a TranscriptDoc
(persistence turns, TUI render, summary payload) must account for each
variant in SEGMENT_TYPES; a type seen at runtime that is NOT here means
the grammar grew a variant a projection forgot.

Mirrors tests/fixtures/transcripts/SCHEMA.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypedDict


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
    # None on single-select menus; the checkbox state on multi-select menus.
    checked: bool | None


class ChoicePromptSegment(TypedDict):
    type: Literal["choice_prompt"]
    question: str
    options: list[ChoiceOptionDict]
    footer: str | None
    # The option number under the dialog cursor; None when the cursor sits on
    # the multi-select's dedicated (unnumbered) Submit row.
    selected: int | None
    answered: bool
    # Single-select: the chosen option number. Multi-select: the list of
    # checked option numbers at resolution. None while unanswered.
    chosen: int | list[int] | None
    # True for a multi-select (CC AskUserQuestion multiSelect) menu.
    multi: bool


Segment = (
    UserSegment
    | AssistantSegment
    | ToolCallSegment
    | PlanUpdateSegment
    | AgentEventSegment
    | ChoicePromptSegment
)

@dataclass(slots=True)
class SpannedSegment:
    """A parsed segment plus the absolute scrollback line range it came from.

    The span is internal-only commitment metadata: ``[start, end)`` in the
    accumulator's absolute (epoch-relative) line coordinates, plus the epoch the
    coordinates belong to. It never reaches ``to_dict`` / the bus payload — the
    accumulator strips it. ``end == start`` (an empty range) marks a pinned
    injected segment (e.g. a live choice prompt) that has no backing scrollback
    lines; commitment treats pinned segments as always-provisional.
    """

    segment: Segment
    start: int
    end: int
    epoch: int = 0

    @property
    def pinned(self) -> bool:
        return self.end <= self.start


# Canonical list of segment `type` discriminants.
SEGMENT_TYPES: tuple[str, ...] = (
    "user",
    "assistant",
    "tool_call",
    "plan_update",
    "agent_event",
    "choice_prompt",
)
