"""Score candidates against a ``RankingPolicy`` profile.

Signal breakdown stays local. Callers see one ``score`` float plus short
``reasons`` strings — not a ten-component published score type.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from murder.context_compiler.candidates.models import (
    CANDIDATE_KIND_DIFF_PATH,
    CANDIDATE_KIND_EXACT_RANGE,
    CANDIDATE_KIND_FILE,
    CANDIDATE_KIND_RELATIONSHIP_NEIGHBOR,
    CANDIDATE_KIND_SEMANTIC_UNIT,
    CANDIDATE_KIND_TEST,
    SCORE_ACTIVE_DIFF_OVERLAP,
    SCORE_AMBIGUOUS_PATH,
    SCORE_AMBIGUOUS_SYMBOL,
    SCORE_DIRECT_LEXICAL,
    SCORE_DIRECT_STRUCTURAL,
    SCORE_EXACT_PATH,
    SCORE_EXACT_QUALIFIED_SYMBOL,
    SCORE_EXACT_UNIQUE_SYMBOL,
    SCORE_FOCUSED_TEST,
    SCORE_WEAK_TEXTUAL,
    Candidate,
    merge_candidates,
)
from murder.context_compiler.candidates.tests import is_test_path
from murder.context_compiler.indexing.resolution_policy import (
    CONFIDENCE_EXACT,
    CONFIDENCE_INFERRED,
    CONFIDENCE_WEAK,
    normalize_confidence,
)
from murder.context_compiler.models import EvidenceCategory
from murder.context_compiler.ranking.models import RangeProposal
from murder.context_compiler.ranking.policy import (
    CATEGORY_SORT_PRIORITY,
    LARGE_UNIT_LINE_THRESHOLD,
    ProfileWeights,
    is_generated_or_vendored,
)


@dataclass(frozen=True, slots=True)
class ScoredCandidate:
    """Scored candidate kept inside the ranking pipeline only."""

    candidate: Candidate
    score: float
    reasons: tuple[str, ...]
    category: EvidenceCategory
    hop: int
    estimated_tokens: int


def ranking_identity(candidate: Candidate) -> tuple[Any, ...]:  # noqa: PLR0911
    """Identity for ranking merge.

    Distinct kinds / ranges over one unit stay separate. Do not collapse on
    ``unit_id`` / ``unit_version_id`` alone — a declaration, call site, test,
    and hinted range are different useful things.
    """
    kind = candidate.candidate_kind
    if kind == CANDIDATE_KIND_EXACT_RANGE:
        return ("range", candidate.path, candidate.start_line, candidate.end_line, kind)
    if kind in {
        CANDIDATE_KIND_SEMANTIC_UNIT,
        CANDIDATE_KIND_TEST,
        CANDIDATE_KIND_RELATIONSHIP_NEIGHBOR,
    }:
        if candidate.unit_version_id is not None:
            return ("unit_version", candidate.unit_version_id, kind)
        if candidate.unit_id is not None:
            return ("unit", candidate.path, candidate.unit_id, kind)
    if candidate.start_line is not None and candidate.end_line is not None:
        return ("range", candidate.path, candidate.start_line, candidate.end_line, kind)
    if candidate.unit_version_id is not None:
        return ("unit_version", candidate.unit_version_id, kind)
    if candidate.unit_id is not None:
        return ("unit", candidate.path, candidate.unit_id, kind)
    return ("file", candidate.path, kind)


def merge_ranked(existing: Candidate, incoming: Candidate) -> Candidate:
    """Merge same-identity candidates; preserve all providers and reasons."""
    return merge_candidates(existing, incoming)


def provider_count(candidate: Candidate) -> int:
    meta = candidate.metadata
    providers = meta.get("providers")
    if isinstance(providers, (list, tuple)) and providers:
        return len({str(p) for p in providers})
    return 1


def _confidence_tier(candidate: Candidate) -> str | None:
    raw = candidate.metadata.get("confidence")
    if raw is None:
        return None
    try:
        return normalize_confidence(raw if isinstance(raw, (str, float)) else str(raw))
    except ValueError:
        return None


_EXACT_SCORES = frozenset(
    {
        SCORE_EXACT_PATH,
        SCORE_EXACT_QUALIFIED_SYMBOL,
        SCORE_EXACT_UNIQUE_SYMBOL,
        SCORE_AMBIGUOUS_PATH,
        SCORE_AMBIGUOUS_SYMBOL,
    }
)


def _has_exact_hint_signal(candidate: Candidate) -> bool:
    if candidate.provider == "exact_hints":
        return True
    if any(
        r.startswith("exact_") or r in {"path_hint", "symbol_hint", "qualified_symbol"}
        for r in candidate.reasons
    ):
        return True
    return candidate.raw_score in _EXACT_SCORES


def _has_active_diff(candidate: Candidate) -> bool:
    if candidate.provider == "active_diff" or candidate.candidate_kind == CANDIDATE_KIND_DIFF_PATH:
        return True
    if candidate.raw_score == SCORE_ACTIVE_DIFF_OVERLAP:
        return True
    return any("diff" in r for r in candidate.reasons)


def _has_strong_lexical(candidate: Candidate) -> bool:
    if candidate.raw_score == SCORE_WEAK_TEXTUAL:
        return False
    if candidate.raw_score == SCORE_DIRECT_LEXICAL:
        return True
    if any(r.startswith("lexical") and "weak" not in r for r in candidate.reasons):
        return True
    if candidate.provider == "lexical":
        return candidate.raw_score is not None and candidate.raw_score >= SCORE_DIRECT_LEXICAL
    return False


def _has_weak_lexical(candidate: Candidate) -> bool:
    if candidate.raw_score == SCORE_WEAK_TEXTUAL:
        return True
    if candidate.metadata.get("heuristic") in {
        "filename_convention",
        "filename",
        "stem",
    }:
        return True
    return any("weak" in r or "filename" in r or "heuristic" in r for r in candidate.reasons)


def _has_direct_structural(candidate: Candidate) -> bool:
    is_structural = (
        candidate.candidate_kind == CANDIDATE_KIND_RELATIONSHIP_NEIGHBOR
        or candidate.provider in {"structural", "ranking_expansion"}
        or candidate.raw_score == SCORE_DIRECT_STRUCTURAL
        or any(r.startswith("structural_") or r.startswith("expand:") for r in candidate.reasons)
    )
    if not is_structural:
        return False
    tier = _confidence_tier(candidate)
    return tier != CONFIDENCE_WEAK


def _has_focused_test(candidate: Candidate) -> bool:
    if candidate.candidate_kind == CANDIDATE_KIND_TEST:
        # Filename-only affinity is weak; explicit test evidence is focused.
        if _has_weak_lexical(candidate) and candidate.raw_score != SCORE_FOCUSED_TEST:
            return False
        return True
    return candidate.provider == "tests" and (
        candidate.raw_score == SCORE_FOCUSED_TEST
        or any("test" in r and "filename" not in r for r in candidate.reasons)
    )


def infer_category(candidate: Candidate) -> EvidenceCategory:  # noqa: PLR0911
    """Map provider/kind/reasons onto an evidence category."""
    if _has_active_diff(candidate):
        return EvidenceCategory.CURRENT_DIFF
    if _has_focused_test(candidate) or candidate.candidate_kind == CANDIDATE_KIND_TEST:
        return EvidenceCategory.TEST
    if is_test_path(candidate.path) and (
        candidate.unit_version_id is not None
        or any("test" in r for r in candidate.reasons)
        or candidate.provider == "tests"
    ):
        return EvidenceCategory.TEST
    if _has_exact_hint_signal(candidate):
        return EvidenceCategory.EDIT_TARGET
    if any(
        r.startswith("structural_template")
        or r.startswith("structural_style")
        or "template_of" in r
        or "style_of" in r
        or r.startswith("expand:template_of")
        or r.startswith("expand:style_of")
        or r.startswith("expand:resource:")
        for r in candidate.reasons
    ):
        return EvidenceCategory.SUPPORTING_CONTEXT
    if candidate.candidate_kind == CANDIDATE_KIND_SEMANTIC_UNIT and (
        candidate.metadata.get("exported") is True
        or any("export" in r or "contract" in r for r in candidate.reasons)
    ):
        return EvidenceCategory.CONTRACT
    if _has_direct_structural(candidate):
        # Public contracts via imports/inherits/implements lean contract.
        rel = str(candidate.metadata.get("relation_kind") or "")
        if rel in {"imports", "inherits", "implements", "exports"}:
            return EvidenceCategory.CONTRACT
        return EvidenceCategory.SUPPORTING_CONTEXT
    if candidate.candidate_kind == CANDIDATE_KIND_FILE:
        return EvidenceCategory.SUPPORTING_CONTEXT
    return EvidenceCategory.OTHER


def score_candidate(  # noqa: PLR0912, PLR0915
    candidate: Candidate,
    weights: ProfileWeights,
    *,
    hop: int = 0,
    estimated_tokens: int = 0,
) -> ScoredCandidate:
    """Compute one score from present signals; reasons list what fired."""
    reasons: list[str] = list(candidate.reasons)
    total = 0.0

    if _has_exact_hint_signal(candidate):
        total += weights.exact_hint
        reasons.append("signal:exact_hint")
    if _has_active_diff(candidate):
        total += weights.active_diff
        reasons.append("signal:active_diff")
    if _has_strong_lexical(candidate):
        total += weights.strong_lexical
        reasons.append("signal:strong_lexical")
    elif _has_weak_lexical(candidate):
        total += weights.weak_lexical
        reasons.append("signal:weak_lexical")
    if _has_direct_structural(candidate):
        total += weights.direct_structural
        reasons.append("signal:direct_structural")
    if _has_focused_test(candidate):
        total += weights.focused_test
        reasons.append("signal:focused_test")

    agreement = provider_count(candidate)
    if agreement > 1:
        bonus = weights.provider_agreement * (agreement - 1)
        total += bonus
        reasons.append(f"signal:provider_agreement:{agreement}")

    # Merge provenance: providers metadata must survive into RangeProposal
    # reasons (the frozen proposal model has no metadata field).
    providers_meta = candidate.metadata.get("providers")
    if isinstance(providers_meta, (list, tuple)) and providers_meta:
        prov = tuple(dict.fromkeys(str(p) for p in providers_meta))
    else:
        prov = (candidate.provider,) if candidate.provider else ()
    if prov:
        reasons.append(f"providers:{','.join(prov)}")

    tier = _confidence_tier(candidate)
    if tier == CONFIDENCE_WEAK:
        total -= weights.weak_tier
        reasons.append("penalty:weak_tier")
    elif tier in {CONFIDENCE_EXACT, CONFIDENCE_INFERRED}:
        reasons.append(f"tier:{tier}")

    if hop > 0:
        total -= weights.relationship_distance * hop
        reasons.append(f"penalty:distance:{hop}")

    if estimated_tokens > 0:
        scaled = (estimated_tokens / 100.0) * weights.token_cost * weights.token_penalty_scale
        total -= scaled
        reasons.append(f"penalty:tokens:{estimated_tokens}")

    unit_lines = 0
    if candidate.start_line is not None and candidate.end_line is not None:
        unit_lines = candidate.end_line - candidate.start_line + 1
    # Rough oversized check before shaping has exact tokens.
    if unit_lines > LARGE_UNIT_LINE_THRESHOLD or estimated_tokens > weights.unit_token_cap:
        total -= weights.large_unit
        reasons.append("penalty:large_unit")

    if is_generated_or_vendored(candidate.path):
        total -= weights.generated_vendored
        reasons.append("penalty:generated_or_vendored")

    # Prefer provider raw_score as a mild tie-break boost when no strong signal
    # fired (keeps relative provider ordering without dominating weights).
    if total == 0.0 and candidate.raw_score is not None:
        total = float(candidate.raw_score) * 0.25
        reasons.append("signal:raw_score_fallback")

    deduped = tuple(dict.fromkeys(reasons))
    category = infer_category(candidate)
    return ScoredCandidate(
        candidate=candidate,
        score=total,
        reasons=deduped,
        category=category,
        hop=hop,
        estimated_tokens=estimated_tokens,
    )


def scored_sort_key(scored: ScoredCandidate) -> tuple[Any, ...]:
    """Descending score, category priority, path, start line, then candidate identity.

    ``ranking_identity`` is the candidate-identity tie-break (spec wording).
    ``end_line`` is included before identity so equal-start spans still order
    stably when identity alone would not distinguish them.
    """
    c = scored.candidate
    cat_pri = CATEGORY_SORT_PRIORITY.get(scored.category.value, 99)
    return (
        -scored.score,
        cat_pri,
        c.path,
        c.start_line if c.start_line is not None else -1,
        c.end_line if c.end_line is not None else -1,
        ranking_identity(c),
    )


def range_proposal_sort_key(proposal: RangeProposal) -> tuple[Any, ...]:
    """Same policy as :func:`scored_sort_key` for shaped ``RangeProposal`` rows.

    Spec order: score ↓, category priority, path, start line, then candidate
    identity. ``RangeProposal`` has no ``Candidate``, so the final tie-break is
    an intentional synthetic identity key
    ``(path, start, end, unit_version_id, category)`` — same role as
    ``ranking_identity``, not the same tuple shape. Not unit_version alone.
    """
    cat_pri = CATEGORY_SORT_PRIORITY.get(proposal.category.value, 99)
    # Synthetic candidate-identity stand-in for shaped ranges (see docstring).
    identity = (
        proposal.path,
        proposal.line_range.start_line,
        proposal.line_range.end_line,
        proposal.unit_version_id if proposal.unit_version_id is not None else -1,
        proposal.category.value,
    )
    return (
        -proposal.score,
        cat_pri,
        proposal.path,
        proposal.line_range.start_line,
        identity,
    )


def merge_candidate_maps(
    existing: dict[tuple[Any, ...], Candidate],
    incoming: Candidate,
) -> None:
    identity = ranking_identity(incoming)
    prior = existing.get(identity)
    if prior is None:
        existing[identity] = incoming
    else:
        existing[identity] = merge_ranked(prior, incoming)


__all__ = [
    "ScoredCandidate",
    "infer_category",
    "merge_candidate_maps",
    "merge_ranked",
    "provider_count",
    "range_proposal_sort_key",
    "ranking_identity",
    "score_candidate",
    "scored_sort_key",
]
