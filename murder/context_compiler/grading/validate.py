"""Deterministic post-validation of model grades.

The model does not control final ranges. Category selects shape via Step 4's
``reshape_proposal_by_category``; ceilings, exact-hint preservation, and
ordering are code-owned.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from murder.context_compiler.grading.models import Grade, GradeResult, ReasonCode
from murder.context_compiler.grading.trace import GradingTrace
from murder.context_compiler.models import ContextRequest, EvidenceCategory
from murder.context_compiler.ranges import RangeValidationError, clamp_range
from murder.context_compiler.ranking.models import CorpusProposal, RangeProposal
from murder.context_compiler.ranking.policy import DEFAULT_RANKING_POLICY, RankingPolicy
from murder.context_compiler.ranking.shaping import reshape_proposal_by_category
from murder.context_compiler.ranking.tokens import DEFAULT_TOKEN_COUNTER, TokenCounter
from murder.context_compiler.rendering import RenderError, extract_source_slice
from murder.context_compiler.source import FilesystemSourceReader, SourceReadError

_EXCLUDE_REASON_CODES = frozenset(
    {
        ReasonCode.TASK_IRRELEVANT,
        ReasonCode.DUPLICATE_INFORMATION,
        ReasonCode.TOO_WEAK,
        ReasonCode.OVERSIZED_LOW_VALUE,
    }
)

_EXACT_HINT_REASON_MARKERS = (
    "shape:exact_hint",
    "signal:exact_hint",
    "exact_path",
    "exact_symbol",
    "exact_range",
)


def is_exact_hint_proposal(proposal: RangeProposal) -> bool:
    """True when Step 4 marked this range as an exact hint that must survive."""
    for reason in proposal.reasons:
        if reason.startswith(_EXACT_HINT_REASON_MARKERS) or reason in _EXACT_HINT_REASON_MARKERS:
            return True
        if "exact_hint" in reason:
            return True
    return False


def post_validate_grades(  # noqa: PLR0912, PLR0915
    request: ContextRequest,
    proposal: CorpusProposal,
    result: GradeResult,
    *,
    worktree_root: Path,
    conn: sqlite3.Connection | None = None,
    ranking_policy: RankingPolicy | None = None,
    token_counter: TokenCounter | None = None,
    trace: GradingTrace | None = None,
) -> tuple[tuple[RangeProposal, ...], tuple[Grade, ...], int]:
    """Apply the seven post-validation steps.

    Returns ``(ranges, applied_grades, estimated_tokens)``.
    """
    policy = ranking_policy or DEFAULT_RANKING_POLICY
    weights = policy.for_profile(request.recipient_profile)
    counter = token_counter or DEFAULT_TOKEN_COUNTER
    reader = FilesystemSourceReader(worktree_root)

    token_ceiling = weights.max_estimated_tokens
    if request.max_tokens is not None:
        token_ceiling = min(token_ceiling, request.max_tokens)

    n = len(proposal.ranges)
    grades_by_index: dict[int, Grade] = {}
    for raw_grade in result.grades:
        if raw_grade.proposal_index < 0 or raw_grade.proposal_index >= n:
            if trace is not None:
                trace.record(
                    "candidate_rejected",
                    "hallucinated_index",
                    detail=str(raw_grade.proposal_index),
                    proposal_index=raw_grade.proposal_index,
                )
            continue
        # Last grade wins for a repeated index (deterministic over input order).
        grades_by_index[raw_grade.proposal_index] = raw_grade

    selected: list[tuple[float, RangeProposal, Grade | None]] = []
    applied_grades: list[Grade] = []
    per_file: dict[str, int] = {}

    # Preserve exact hints regardless of grade.
    forced_indices: set[int] = set()
    for idx, rng in enumerate(proposal.ranges):
        if is_exact_hint_proposal(rng):
            forced_indices.add(idx)

    for idx, rng in enumerate(proposal.ranges):
        graded = grades_by_index.get(idx)
        force = idx in forced_indices
        include = force or (graded is not None and graded.include)
        if not include:
            if graded is not None and trace is not None:
                trace.record(
                    "candidate_rejected",
                    graded.reason_code.value,
                    path=rng.path,
                    proposal_index=idx,
                )
            elif graded is None and trace is not None:
                trace.record(
                    "candidate_rejected",
                    "ungraded",
                    path=rng.path,
                    proposal_index=idx,
                )
            continue

        category = graded.category if graded is not None else rng.category
        if force and graded is not None and not graded.include:
            if trace is not None:
                trace.record(
                    "grading_repaired",
                    "exact_hint_preserved",
                    path=rng.path,
                    proposal_index=idx,
                )

        shaped_result = reshape_proposal_by_category(
            rng,
            category,
            conn=conn,
            snapshot_id=proposal.snapshot_id,
            source_reader=reader,
            token_counter=counter,
            unit_token_cap=weights.unit_token_cap,
        )
        if shaped_result.proposal is None:
            if trace is not None and shaped_result.reject_reason is not None:
                trace.record(
                    "candidate_rejected",
                    shaped_result.reject_reason,
                    path=rng.path,
                    detail=shaped_result.reject_detail,
                    proposal_index=idx,
                )
            continue
        shaped = shaped_result.proposal
        if shaped_result.repair_reason is not None and trace is not None:
            trace.record(
                "grading_repaired",
                shaped_result.repair_reason,
                path=rng.path,
                proposal_index=idx,
            )

        # Out-of-bounds already rejected inside shaping; re-check against source.
        try:
            snap = reader.read(shaped.path)
            lr = clamp_range(shaped.line_range, snap.line_count)
            tokens = counter.count_tokens(
                extract_source_slice(snap.text, lr.start_line, lr.end_line)
            )
            shaped = RangeProposal(
                path=shaped.path,
                line_range=lr,
                unit_version_id=shaped.unit_version_id,
                category=shaped.category,
                score=shaped.score,
                reasons=shaped.reasons,
                estimated_tokens=tokens,
            )
        except (SourceReadError, RangeValidationError, RenderError, ValueError) as exc:
            if trace is not None:
                trace.record(
                    "candidate_rejected",
                    "out_of_bounds",
                    path=rng.path,
                    detail=str(exc),
                    proposal_index=idx,
                )
            continue

        if per_file.get(shaped.path, 0) >= weights.max_candidates_per_file:
            if trace is not None:
                trace.record(
                    "candidate_rejected",
                    "max_candidates_per_file",
                    path=shaped.path,
                    proposal_index=idx,
                )
            continue

        selected.append((rng.score, shaped, graded))
        if graded is not None:
            applied_grades.append(graded)
            if graded.include and graded.reason_code not in _EXCLUDE_REASON_CODES:
                if trace is not None:
                    trace.record(
                        "candidate_selected",
                        graded.reason_code.value,
                        path=shaped.path,
                        proposal_index=idx,
                    )

    # Deterministic order: original score desc, then path/lines.
    selected.sort(
        key=lambda item: (
            -item[0],
            item[1].category.value,
            item[1].path,
            item[1].line_range.start_line,
            item[1].line_range.end_line,
            item[1].unit_version_id if item[1].unit_version_id is not None else -1,
        )
    )

    # Profile hard ceilings.
    kept: list[RangeProposal] = []
    total = 0
    for _score, shaped, _grade in selected:
        if len(kept) >= weights.max_range_proposals:
            if trace is not None:
                trace.record(
                    "candidate_rejected",
                    "max_range_proposals",
                    path=shaped.path,
                )
            continue
        if total + shaped.estimated_tokens > token_ceiling:
            if trace is not None:
                trace.record(
                    "candidate_rejected",
                    "token_ceiling",
                    path=shaped.path,
                    detail=str(shaped.estimated_tokens),
                )
            continue
        kept.append(shaped)
        per_file[shaped.path] = per_file.get(shaped.path, 0) + 1
        total += shaped.estimated_tokens

    # Stable grade order by proposal_index.
    applied_grades.sort(key=lambda g: g.proposal_index)
    return tuple(kept), tuple(applied_grades), total


def fallback_from_proposal(
    request: ContextRequest,
    proposal: CorpusProposal,
    *,
    worktree_root: Path,
    conn: sqlite3.Connection | None = None,
    ranking_policy: RankingPolicy | None = None,
    token_counter: TokenCounter | None = None,
    trace: GradingTrace | None = None,
) -> tuple[tuple[RangeProposal, ...], tuple[Grade, ...], int]:
    """Fall back to Step 4's top-ranked proposal within budget.

    Synthesizes include=True grades so post-validation still applies ceilings
    and exact-hint preservation. Expansion is skipped by the orchestrator.
    """
    if trace is not None:
        trace.record("grading_failed", "grader_invalid_output")
    synthetic = GradeResult(
        grades=tuple(
            Grade(
                proposal_index=i,
                include=True,
                category=rng.category,
                reason_code=_fallback_reason(rng.category),
            )
            for i, rng in enumerate(proposal.ranges)
        ),
        gaps=None,
    )
    return post_validate_grades(
        request,
        proposal,
        synthetic,
        worktree_root=worktree_root,
        conn=conn,
        ranking_policy=ranking_policy,
        token_counter=token_counter,
        trace=trace,
    )


def _fallback_reason(category: EvidenceCategory) -> ReasonCode:
    if category is EvidenceCategory.EDIT_TARGET:
        return ReasonCode.LIKELY_EDIT_TARGET
    if category is EvidenceCategory.CONTRACT:
        return ReasonCode.REQUIRED_CONTRACT
    if category is EvidenceCategory.TEST:
        return ReasonCode.FOCUSED_TEST
    return ReasonCode.TOO_WEAK


__all__ = [
    "fallback_from_proposal",
    "is_exact_hint_proposal",
    "post_validate_grades",
]
