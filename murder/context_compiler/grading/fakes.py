"""Deterministic fake graders for hermetic Step 5 tests.

Prefer these over mocks. Each fake encodes one behavioural fixture.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from murder.context_compiler.grading.errors import GraderOutputError
from murder.context_compiler.grading.models import Grade, GradeResult, ReasonCode, RequestDelta
from murder.context_compiler.models import ContextRequest, EvidenceCategory
from murder.context_compiler.ranking.models import CorpusProposal

GradeFn = Callable[[ContextRequest, CorpusProposal], GradeResult]


@dataclass
class FakeContextGrader:
    """``ContextGrader`` that returns scripted results, optionally per call."""

    results: list[GradeResult] = field(default_factory=list)
    fn: GradeFn | None = None
    calls: list[tuple[ContextRequest, CorpusProposal]] = field(default_factory=list)
    raise_on_call: int | None = None

    async def grade(
        self,
        request: ContextRequest,
        proposal: CorpusProposal,
    ) -> GradeResult:
        self.calls.append((request, proposal))
        call_n = len(self.calls)
        if self.raise_on_call is not None and call_n == self.raise_on_call:
            raise GraderOutputError("scripted malformed output")
        if self.fn is not None:
            return self.fn(request, proposal)
        if not self.results:
            return _include_all(proposal)
        if len(self.results) == 1:
            return self.results[0]
        return self.results[min(call_n - 1, len(self.results) - 1)]


def _include_all(proposal: CorpusProposal) -> GradeResult:
    grades = tuple(
        Grade(
            proposal_index=i,
            include=True,
            category=rng.category,
            reason_code=_reason_for(rng.category),
        )
        for i, rng in enumerate(proposal.ranges)
    )
    return GradeResult(grades=grades, gaps=None)


def _reason_for(category: EvidenceCategory) -> ReasonCode:
    if category is EvidenceCategory.EDIT_TARGET:
        return ReasonCode.LIKELY_EDIT_TARGET
    if category is EvidenceCategory.CONTRACT:
        return ReasonCode.REQUIRED_CONTRACT
    if category is EvidenceCategory.TEST:
        return ReasonCode.FOCUSED_TEST
    return ReasonCode.TOO_WEAK


def exclude_paths_grader(*paths: str) -> FakeContextGrader:
    """Include everything except proposals whose path is in ``paths``."""
    excluded = frozenset(paths)

    def _fn(_request: ContextRequest, proposal: CorpusProposal) -> GradeResult:
        grades: list[Grade] = []
        for i, rng in enumerate(proposal.ranges):
            if rng.path in excluded:
                grades.append(
                    Grade(
                        proposal_index=i,
                        include=False,
                        category=rng.category,
                        reason_code=ReasonCode.TASK_IRRELEVANT,
                    )
                )
            else:
                grades.append(
                    Grade(
                        proposal_index=i,
                        include=True,
                        category=rng.category,
                        reason_code=_reason_for(rng.category),
                    )
                )
        return GradeResult(grades=tuple(grades), gaps=None)

    return FakeContextGrader(fn=_fn)


def gaps_then_adequate_grader(delta: RequestDelta) -> FakeContextGrader:
    """First call reports ``delta`` gaps; subsequent calls report adequate (no gaps)."""

    def _fn(request: ContextRequest, proposal: CorpusProposal) -> GradeResult:
        base = _include_all(proposal)
        # First call (detected via whether delta hints already applied).
        already = (
            set(request.path_hints)
            | set(request.symbol_hints)
            | set(request.search_terms)
            | set(request.relationship_kind_hints)
        )
        needed = (
            set(delta.path_hints)
            | set(delta.symbol_hints)
            | set(delta.search_terms)
            | set(delta.relationship_kinds)
        )
        if needed and not needed.issubset(already):
            return GradeResult(grades=base.grades, gaps=delta)
        return GradeResult(grades=base.grades, gaps=None)

    return FakeContextGrader(fn=_fn)


def malformed_then_valid_grader(valid: GradeResult) -> FakeContextGrader:
    """Port-level retry-once: fail then succeed inside a single ``grade`` call.

    Mirrors ``LlmContextGrader`` (one retry inside the port). The first attempt
    raises ``GraderOutputError`` and is caught internally; the second returns
    ``valid``. ``CorpusGrader`` does not stack another retry — two attempts total.
    """

    @dataclass
    class _RetryOncePort(FakeContextGrader):
        internal_attempts: int = 0
        failures: list[str] = field(default_factory=list)

        async def grade(
            self,
            request: ContextRequest,
            proposal: CorpusProposal,
        ) -> GradeResult:
            self.calls.append((request, proposal))
            last_error: GraderOutputError | None = None
            # initial attempt + one retry (same bound as LlmContextGrader).
            for attempt in range(2):
                self.internal_attempts += 1
                if attempt == 0:
                    err = GraderOutputError("scripted malformed output")
                    self.failures.append(str(err))
                    last_error = err
                    continue
                return valid
            raise last_error or GraderOutputError("scripted malformed output")

    return _RetryOncePort()


def hallucinated_indices_grader(
    *,
    good: GradeResult,
    extra_indices: tuple[int, ...] = (999, -1),
) -> FakeContextGrader:
    """Return valid grades plus hallucinated indices that post-validation must ignore."""

    def _fn(_request: ContextRequest, proposal: CorpusProposal) -> GradeResult:
        del proposal
        extras = tuple(
            Grade(
                proposal_index=idx,
                include=True,
                category=EvidenceCategory.OTHER,
                reason_code=ReasonCode.TOO_WEAK,
            )
            for idx in extra_indices
            if idx >= 0  # Grade wire forbids negative; domain Grade allows for reject test
        )
        # Inject via object construction for out-of-range positive indices.
        return GradeResult(grades=(*good.grades, *extras), gaps=None)

    return FakeContextGrader(fn=_fn)


def planning_broader_contracts_grader() -> FakeContextGrader:
    """Prefer contract-category includes for planning-style evaluation."""

    def _fn(request: ContextRequest, proposal: CorpusProposal) -> GradeResult:
        grades: list[Grade] = []
        for i, rng in enumerate(proposal.ranges):
            is_contract_path = (
                "contract" in rng.path.lower() or rng.category is EvidenceCategory.CONTRACT
            )
            if request.recipient_profile.value == "compact" and is_contract_path:
                # Compact may still see contracts but we mark supporting weakly;
                # include edit targets preferentially.
                include = rng.category is EvidenceCategory.EDIT_TARGET or is_exact(
                    rng.path, request
                )
                grades.append(
                    Grade(
                        proposal_index=i,
                        include=include,
                        category=rng.category,
                        reason_code=(
                            ReasonCode.LIKELY_EDIT_TARGET if include else ReasonCode.TASK_IRRELEVANT
                        ),
                    )
                )
            else:
                grades.append(
                    Grade(
                        proposal_index=i,
                        include=True,
                        category=(EvidenceCategory.CONTRACT if is_contract_path else rng.category),
                        reason_code=(
                            ReasonCode.REQUIRED_CONTRACT
                            if is_contract_path
                            else _reason_for(rng.category)
                        ),
                    )
                )
        return GradeResult(grades=tuple(grades), gaps=None)

    return FakeContextGrader(fn=_fn)


def is_exact(path: str, request: ContextRequest) -> bool:
    return path in request.path_hints or any(path.endswith(h) for h in request.path_hints)


__all__ = [
    "FakeContextGrader",
    "exclude_paths_grader",
    "gaps_then_adequate_grader",
    "hallucinated_indices_grader",
    "malformed_then_valid_grader",
    "planning_broader_contracts_grader",
]
