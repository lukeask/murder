"""Typed evaluation case shapes.

``expected`` collapses the earlier required/acceptable split: one recall
number. ``forbidden`` catches precision / noise failures.
"""

from __future__ import annotations

from dataclasses import dataclass

from murder.context_compiler.models import RecipientProfile


@dataclass(frozen=True, slots=True)
class UnitRef:
    """Stable ``path::unit`` identity used in expectations."""

    path: str
    unit: str

    def key(self) -> str:
        return f"{self.path}::{self.unit}"


@dataclass(frozen=True, slots=True)
class RangeRef:
    """Expected path + inclusive line span for Step 4 range recall."""

    path: str
    start_line: int
    end_line: int

    def key(self) -> str:
        return f"{self.path}:{self.start_line}-{self.end_line}"

    def overlaps(self, path: str, start: int, end: int) -> bool:
        if path != self.path:
            return False
        return not (end < self.start_line or start > self.end_line)


@dataclass(frozen=True, slots=True)
class EvalCase:
    name: str
    objective: str
    profile: RecipientProfile
    fixture_shape: str
    symbol_hints: tuple[str, ...] = ()
    path_hints: tuple[str, ...] = ()
    expected: tuple[UnitRef, ...] = ()
    forbidden: tuple[UnitRef, ...] = ()
    top_k: int = 20
    # Step 4: optional range expectations and mode switch.
    expected_ranges: tuple[RangeRef, ...] = ()
    forbidden_ranges: tuple[RangeRef, ...] = ()
    # "candidates" (Step 3), "corpus" (Step 4), or "graded" (Step 5 fake grade).
    mode: str = "candidates"
    # Soft cap check for corpus mode (None = use profile policy).
    max_tokens: int | None = None
    # Compare token totals against another case name (compact < implementation).
    expect_fewer_tokens_than: str | None = None
    # Step 5 graded mode: fake-grader recipe (no model calls).
    grader_exclude_paths: tuple[str, ...] = ()
    grader_gap_search_terms: tuple[str, ...] = ()
    grader_gap_relationship_kinds: tuple[str, ...] = ()
    # When set, graded report must match this expansion_rounds count.
    expect_expansion_rounds: int | None = None


@dataclass(frozen=True, slots=True)
class EvalCaseReport:
    name: str
    candidate_count: int
    expected_unit_recall: float
    top_k_recall: float
    forbidden_unit_hits: int
    provider_attribution: tuple[str, ...]
    determinism_status: str
    hit_expected: tuple[str, ...]
    missed_expected: tuple[str, ...]
    hit_forbidden: tuple[str, ...]
    # Step 4 corpus metrics (zeroed for candidate-mode cases).
    estimated_tokens: int = 0
    top_k_range_recall: float = 0.0
    max_expansion_distance: int = 0
    truncated: bool = False
    hit_expected_ranges: tuple[str, ...] = ()
    missed_expected_ranges: tuple[str, ...] = ()
    hit_forbidden_ranges: tuple[str, ...] = ()
    # None when the case does not declare expect_fewer_tokens_than.
    fewer_tokens_than_ok: bool | None = None
    # Step 5 graded metrics (zeroed for non-graded cases).
    expansion_rounds: int = 0
    used_fallback: bool = False
    expansion_rounds_ok: bool | None = None


@dataclass(frozen=True, slots=True)
class EvalReport:
    cases: tuple[EvalCaseReport, ...]

    @property
    def all_deterministic(self) -> bool:
        return all(c.determinism_status == "identical" for c in self.cases)

    @property
    def all_token_comparisons_ok(self) -> bool:
        return all(c.fewer_tokens_than_ok is not False for c in self.cases)

    @property
    def all_expansion_rounds_ok(self) -> bool:
        return all(c.expansion_rounds_ok is not False for c in self.cases)

    @property
    def mean_expected_recall(self) -> float:
        if not self.cases:
            return 1.0
        return sum(c.expected_unit_recall for c in self.cases) / len(self.cases)

    def case_named(self, name: str) -> EvalCaseReport | None:
        for case in self.cases:
            if case.name == name:
                return case
        return None


def case_identity(case: EvalCase) -> tuple[object, ...]:
    """Byte-stable identity for determinism checks."""
    profile = (
        case.profile.value if isinstance(case.profile, RecipientProfile) else str(case.profile)
    )
    return (
        case.name,
        case.objective,
        profile,
        case.fixture_shape,
        case.symbol_hints,
        case.path_hints,
        tuple(u.key() for u in case.expected),
        tuple(u.key() for u in case.forbidden),
        case.top_k,
        tuple(r.key() for r in case.expected_ranges),
        tuple(r.key() for r in case.forbidden_ranges),
        case.mode,
        case.max_tokens,
        case.expect_fewer_tokens_than,
        case.grader_exclude_paths,
        case.grader_gap_search_terms,
        case.grader_gap_relationship_kinds,
        case.expect_expansion_rounds,
    )


__all__ = [
    "EvalCase",
    "EvalCaseReport",
    "EvalReport",
    "RangeRef",
    "UnitRef",
    "case_identity",
]
