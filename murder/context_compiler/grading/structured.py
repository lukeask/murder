"""Pydantic wire schemas for validated structured grading output.

Domain ``Grade`` / ``GradeResult`` stay free of pydantic. Parsing goes through
``TypeAdapter`` — the project's typed validation facility.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from murder.context_compiler.grading.delta import bound_delta
from murder.context_compiler.grading.models import Grade, GradeResult, ReasonCode, RequestDelta
from murder.context_compiler.models import EvidenceCategory

ReasonCodeLiteral = Literal[
    "likely_edit_target",
    "required_contract",
    "direct_caller",
    "direct_callee",
    "focused_test",
    "nonlocal_consequence",
    "framework_resource",
    "configuration_owner",
    "task_irrelevant",
    "duplicate_information",
    "too_weak",
    "oversized_low_value",
]

CategoryLiteral = Literal[
    "edit_target",
    "contract",
    "supporting_context",
    "test",
    "verification",
    "current_diff",
    "other",
]


class GradeWire(BaseModel):
    """One graded proposal item on the wire."""

    model_config = ConfigDict(extra="forbid")

    proposal_index: int = Field(ge=0)
    include: bool
    category: CategoryLiteral
    reason_code: ReasonCodeLiteral
    # Optional one-sentence rationale for traces only — never persisted as CoT.
    rationale: str | None = Field(default=None, max_length=240)


class GapsWire(BaseModel):
    """Optional adequacy gaps — hints only."""

    model_config = ConfigDict(extra="forbid")

    path_hints: list[str] = Field(default_factory=list)
    symbol_hints: list[str] = Field(default_factory=list)
    search_terms: list[str] = Field(default_factory=list)
    relationship_kinds: list[str] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)


class GradeResultWire(BaseModel):
    """Full structured grading response."""

    model_config = ConfigDict(extra="forbid")

    grades: list[GradeWire]
    gaps: GapsWire | None = None


GRADE_RESULT_ADAPTER: TypeAdapter[GradeResultWire] = TypeAdapter(GradeResultWire)

SUBMIT_GRADES_TOOL_NAME = "submit_grades"


def grade_result_json_schema() -> dict[str, Any]:
    """JSON Schema for the ``submit_grades`` tool parameters."""
    schema = GRADE_RESULT_ADAPTER.json_schema()
    # Ensure root is an object schema suitable for tool parameters.
    if not isinstance(schema, dict):
        raise TypeError("expected dict JSON schema")
    return schema


def parse_grade_result(payload: object) -> GradeResult:
    """Validate ``payload`` and convert to domain ``GradeResult``.

    Raises ``ValidationError`` on malformed input.
    """
    wire = GRADE_RESULT_ADAPTER.validate_python(payload)
    grades = tuple(
        Grade(
            proposal_index=item.proposal_index,
            include=item.include,
            category=EvidenceCategory(item.category),
            reason_code=ReasonCode(item.reason_code),
        )
        for item in wire.grades
    )
    gaps: RequestDelta | None = None
    if wire.gaps is not None:
        delta = bound_delta(
            RequestDelta(
                path_hints=tuple(wire.gaps.path_hints),
                symbol_hints=tuple(wire.gaps.symbol_hints),
                search_terms=tuple(wire.gaps.search_terms),
                relationship_kinds=tuple(wire.gaps.relationship_kinds),
                unresolved_questions=tuple(wire.gaps.unresolved_questions),
            )
        )
        if not delta.is_empty():
            gaps = delta
    return GradeResult(grades=grades, gaps=gaps)


def parse_grade_result_json(text: str) -> GradeResult:
    """Parse JSON text into a domain ``GradeResult``."""
    data = json.loads(text)
    return parse_grade_result(data)


def extract_rationales(payload: object) -> dict[int, str]:
    """Pull optional per-grade rationales for traces (never recipient output)."""
    try:
        wire = GRADE_RESULT_ADAPTER.validate_python(payload)
    except ValidationError:
        return {}
    out: dict[int, str] = {}
    for item in wire.grades:
        if item.rationale:
            out[item.proposal_index] = item.rationale.strip()[:240]
    return out


__all__ = [
    "GRADE_RESULT_ADAPTER",
    "SUBMIT_GRADES_TOOL_NAME",
    "GapsWire",
    "GradeResultWire",
    "GradeWire",
    "ValidationError",
    "extract_rationales",
    "grade_result_json_schema",
    "parse_grade_result",
    "parse_grade_result_json",
]
