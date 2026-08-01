"""Corpus grading orchestration — grade + at most one expansion round.

``ContextGrader.grade`` is the model port. This module owns expansion,
fallback, post-validation, and the final ``GradedCorpus``.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from murder.context_compiler.candidates.models import SnapshotRef
from murder.context_compiler.grading.delta import apply_request_delta, bound_delta
from murder.context_compiler.grading.errors import GraderOutputError
from murder.context_compiler.grading.models import GradedCorpus, GradeResult, RequestDelta
from murder.context_compiler.grading.policy import MAX_EXPANSION_ROUNDS, MAX_UNRESOLVED_QUESTIONS
from murder.context_compiler.grading.ports import ContextGrader
from murder.context_compiler.grading.trace import GradingTrace
from murder.context_compiler.grading.validate import fallback_from_proposal, post_validate_grades
from murder.context_compiler.models import ContextRequest
from murder.context_compiler.ranking.models import CorpusProposal
from murder.context_compiler.ranking.policy import DEFAULT_RANKING_POLICY, RankingPolicy
from murder.context_compiler.ranking.propose import CorpusProposer, build_corpus_proposer
from murder.context_compiler.ranking.tokens import DEFAULT_TOKEN_COUNTER, TokenCounter

ProposeFn = Callable[
    [ContextRequest, SnapshotRef],
    Awaitable[CorpusProposal],
]


@dataclass
class CorpusGrader:
    """Bound dependencies for ``grade_corpus(request, proposal, snapshot)``."""

    grader: ContextGrader
    worktree_root: Path
    conn: sqlite3.Connection | None = None
    proposer: CorpusProposer | None = None
    ranking_policy: RankingPolicy = field(default_factory=lambda: DEFAULT_RANKING_POLICY)
    token_counter: TokenCounter = field(default_factory=lambda: DEFAULT_TOKEN_COUNTER)
    _last_trace: GradingTrace | None = field(default=None, init=False, repr=False)

    @property
    def last_trace(self) -> GradingTrace | None:
        return self._last_trace

    async def grade_corpus(
        self,
        request: ContextRequest,
        proposal: CorpusProposal,
        snapshot: SnapshotRef,
        *,
        trace: GradingTrace | None = None,
        propose: ProposeFn | None = None,
    ) -> GradedCorpus:
        """Grade ``proposal``, expand at most once, return a bounded corpus."""
        active = trace if trace is not None else GradingTrace()
        self._last_trace = active
        active.record("grading_started", "corpus", detail=request.recipient_profile.value)

        used_fallback = False
        expansion_rounds = 0
        current_request = request
        current_proposal = proposal

        grade_result = await self._grade_once(current_request, current_proposal)
        if grade_result is None:
            used_fallback = True
            ranges, grades, tokens = fallback_from_proposal(
                current_request,
                current_proposal,
                worktree_root=self.worktree_root,
                conn=self.conn,
                ranking_policy=self.ranking_policy,
                token_counter=self.token_counter,
                trace=active,
            )
            active.record("final_grade", "fallback")
            return GradedCorpus(
                snapshot_id=current_proposal.snapshot_id,
                profile=current_request.recipient_profile,
                ranges=ranges,
                grades=grades,
                estimated_tokens=tokens,
                unresolved_questions=(),
                expansion_rounds=0,
                used_fallback=True,
            )

        # Optional single expansion round.
        if (
            grade_result.gaps is not None
            and not grade_result.gaps.is_empty()
            and MAX_EXPANSION_ROUNDS >= 1
        ):
            delta = bound_delta(grade_result.gaps)
            propose_fn = propose or self._default_propose()
            active.record(
                "expansion_requested",
                "gaps",
                detail=_delta_detail(delta),
            )
            if propose_fn is None:
                # Coherent pair: requested but could not re-propose.
                active.record(
                    "expansion_failed",
                    "repropose_unavailable",
                    detail=_delta_detail(delta),
                )
                blocked_unresolved = _unresolved_from_delta(delta)
                ranges, grades, tokens = post_validate_grades(
                    current_request,
                    current_proposal,
                    grade_result,
                    worktree_root=self.worktree_root,
                    conn=self.conn,
                    ranking_policy=self.ranking_policy,
                    token_counter=self.token_counter,
                    trace=active,
                )
                active.record("final_grade", "expansion_unavailable")
                return GradedCorpus(
                    snapshot_id=current_proposal.snapshot_id,
                    profile=current_request.recipient_profile,
                    ranges=ranges,
                    grades=grades,
                    estimated_tokens=tokens,
                    unresolved_questions=blocked_unresolved,
                    expansion_rounds=0,
                    used_fallback=False,
                )

            current_request = apply_request_delta(current_request, delta)
            current_proposal = await propose_fn(current_request, snapshot)
            expansion_rounds = 1
            active.record(
                "expansion_completed",
                "reproposed",
                detail=f"ranges={len(current_proposal.ranges)}",
            )
            grade_result = await self._grade_once(current_request, current_proposal)
            if grade_result is None:
                used_fallback = True
                ranges, grades, tokens = fallback_from_proposal(
                    current_request,
                    current_proposal,
                    worktree_root=self.worktree_root,
                    conn=self.conn,
                    ranking_policy=self.ranking_policy,
                    token_counter=self.token_counter,
                    trace=active,
                )
                unresolved_after = _unresolved_from_delta(delta)
                active.record("final_grade", "fallback_after_expansion")
                return GradedCorpus(
                    snapshot_id=current_proposal.snapshot_id,
                    profile=current_request.recipient_profile,
                    ranges=ranges,
                    grades=grades,
                    estimated_tokens=tokens,
                    unresolved_questions=unresolved_after,
                    expansion_rounds=expansion_rounds,
                    used_fallback=True,
                )

        assert grade_result is not None
        ranges, grades, tokens = post_validate_grades(
            current_request,
            current_proposal,
            grade_result,
            worktree_root=self.worktree_root,
            conn=self.conn,
            ranking_policy=self.ranking_policy,
            token_counter=self.token_counter,
            trace=active,
        )

        unresolved: tuple[str, ...] = ()
        if grade_result.gaps is not None and not grade_result.gaps.is_empty():
            # Gaps after the final pass — do not expand again.
            unresolved = _unresolved_from_delta(bound_delta(grade_result.gaps))
            active.record(
                "final_grade",
                "unresolved_gaps",
                detail=";".join(unresolved[:MAX_UNRESOLVED_QUESTIONS]),
            )
        else:
            active.record("final_grade", "adequate" if expansion_rounds == 0 else "after_expansion")

        return GradedCorpus(
            snapshot_id=current_proposal.snapshot_id,
            profile=current_request.recipient_profile,
            ranges=ranges,
            grades=grades,
            estimated_tokens=tokens,
            unresolved_questions=unresolved,
            expansion_rounds=expansion_rounds,
            used_fallback=used_fallback,
        )

    async def _grade_once(
        self,
        request: ContextRequest,
        proposal: CorpusProposal,
    ) -> GradeResult | None:
        """Call the grader once; ``None`` → fallback.

        Retry-once for malformed structured output lives in the ``ContextGrader``
        implementation (``LlmContextGrader``), not here — stacking another retry
        would exceed the policy intent of one total retry before fallback.
        Trace failure events are recorded by the adapter and ``fallback_from_proposal``.
        """
        try:
            return await self.grader.grade(request, proposal)
        except GraderOutputError:
            return None

    def _default_propose(self) -> ProposeFn | None:
        if self.proposer is not None:
            proposer = self.proposer

            async def _via_proposer(
                request: ContextRequest, snapshot: SnapshotRef
            ) -> CorpusProposal:
                return await proposer.propose_corpus(request, snapshot)

            return _via_proposer
        if self.conn is None:
            return None
        built = build_corpus_proposer(
            self.conn,
            worktree_root=self.worktree_root,
            policy=self.ranking_policy,
            token_counter=self.token_counter,
        )

        async def _via_built(request: ContextRequest, snapshot: SnapshotRef) -> CorpusProposal:
            return await built.propose_corpus(request, snapshot)

        return _via_built


def _delta_detail(delta: RequestDelta) -> str:
    parts: list[str] = []
    if delta.path_hints:
        parts.append(f"paths={','.join(delta.path_hints)}")
    if delta.symbol_hints:
        parts.append(f"symbols={','.join(delta.symbol_hints)}")
    if delta.search_terms:
        parts.append(f"terms={','.join(delta.search_terms)}")
    if delta.relationship_kinds:
        parts.append(f"rels={','.join(delta.relationship_kinds)}")
    return ";".join(parts)


def _unresolved_from_delta(delta: RequestDelta) -> tuple[str, ...]:
    if delta.unresolved_questions:
        return delta.unresolved_questions[:MAX_UNRESOLVED_QUESTIONS]
    parts: list[str] = []
    for path in delta.path_hints:
        parts.append(f"missing path hint: {path}")
    for sym in delta.symbol_hints:
        parts.append(f"missing symbol hint: {sym}")
    for term in delta.search_terms:
        parts.append(f"missing search term: {term}")
    for kind in delta.relationship_kinds:
        parts.append(f"missing relationship kind: {kind}")
    return tuple(parts[:MAX_UNRESOLVED_QUESTIONS])


def build_corpus_grader(
    grader: ContextGrader,
    *,
    worktree_root: Path | str,
    conn: sqlite3.Connection | None = None,
    proposer: CorpusProposer | None = None,
    ranking_policy: RankingPolicy | None = None,
    token_counter: TokenCounter | None = None,
) -> CorpusGrader:
    """Factory for a corpus grader bound to worktree (and optional index)."""
    return CorpusGrader(
        grader=grader,
        worktree_root=Path(worktree_root),
        conn=conn,
        proposer=proposer,
        ranking_policy=ranking_policy or DEFAULT_RANKING_POLICY,
        token_counter=token_counter or DEFAULT_TOKEN_COUNTER,
    )


__all__ = [
    "CorpusGrader",
    "build_corpus_grader",
]
