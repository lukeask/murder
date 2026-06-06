"""Typed segment schema for transcript parsing.

Harness-agnostic, zero imports.  Every projection of a TranscriptDoc
(persistence turns, TUI render, summary payload) must account for each
variant in SEGMENT_TYPES; a type seen at runtime that is NOT here means
the grammar grew a variant a projection forgot.

Mirrors tests/fixtures/transcripts/SCHEMA.md.
"""

from __future__ import annotations

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

# Canonical list of segment `type` discriminants.
SEGMENT_TYPES: tuple[str, ...] = (
    "user",
    "assistant",
    "tool_call",
    "plan_update",
    "agent_event",
    "choice_prompt",
)
