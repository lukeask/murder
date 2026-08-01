"""Frozen grading records for Step 5 cheap-model grading.

Domain types depend on none of ``murder.llm``. Model deliberation stays in
traces; recipient-facing briefs never receive rationale prose.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from murder.context_compiler.models import EvidenceCategory, RecipientProfile
from murder.context_compiler.ranking.models import RangeProposal


class ReasonCode(str, Enum):
    """Closed vocabulary of grade reasons. No free-form chain-of-thought."""

    LIKELY_EDIT_TARGET = "likely_edit_target"
    REQUIRED_CONTRACT = "required_contract"
    DIRECT_CALLER = "direct_caller"
    DIRECT_CALLEE = "direct_callee"
    FOCUSED_TEST = "focused_test"
    NONLOCAL_CONSEQUENCE = "nonlocal_consequence"
    FRAMEWORK_RESOURCE = "framework_resource"
    CONFIGURATION_OWNER = "configuration_owner"
    TASK_IRRELEVANT = "task_irrelevant"
    DUPLICATE_INFORMATION = "duplicate_information"
    TOO_WEAK = "too_weak"
    OVERSIZED_LOW_VALUE = "oversized_low_value"


@dataclass(frozen=True, slots=True)
class Grade:
    """One model judgement over a proposal index.

    ``include`` and ``category`` are signals inside deterministic bounds —
    post-validation shapes ranges and applies ceilings.
    """

    proposal_index: int
    include: bool
    category: EvidenceCategory
    reason_code: ReasonCode


@dataclass(frozen=True, slots=True)
class RequestDelta:
    """Hints describing what is missing — never an operation vocabulary.

    Fed into Step 2 providers and Step 4 re-ranking. Every list is bounded by
    grading policy constants at parse/apply time.
    """

    path_hints: tuple[str, ...] = ()
    symbol_hints: tuple[str, ...] = ()
    search_terms: tuple[str, ...] = ()
    relationship_kinds: tuple[str, ...] = ()
    unresolved_questions: tuple[str, ...] = ()

    def is_empty(self) -> bool:
        return not (
            self.path_hints
            or self.symbol_hints
            or self.search_terms
            or self.relationship_kinds
            or self.unresolved_questions
        )


@dataclass(frozen=True, slots=True)
class GradeResult:
    """Validated model output for one grading pass."""

    grades: tuple[Grade, ...]
    gaps: RequestDelta | None


@dataclass(frozen=True, slots=True)
class GradedCorpus:
    """Final bounded corpus after grading, post-validation, and at most one expansion.

    ``ranges`` are exact source proposals — no synthesis. ``unresolved_questions``
    records gaps that remain after the final grading pass.
    """

    snapshot_id: int
    profile: RecipientProfile
    ranges: tuple[RangeProposal, ...]
    grades: tuple[Grade, ...]
    estimated_tokens: int
    unresolved_questions: tuple[str, ...]
    expansion_rounds: int
    used_fallback: bool


__all__ = [
    "Grade",
    "GradeResult",
    "GradedCorpus",
    "ReasonCode",
    "RequestDelta",
]
