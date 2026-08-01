"""Candidate shapes and scoring constants for Context Assembler 2 providers.

These types supersede Step 0 ``CandidateRecord`` for snapshot-scoped retrieval.
Step 0 ports remain unchanged; adapt at the composite boundary if needed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Explicit per-source scores (Part 10). Not a universal formula.
# ---------------------------------------------------------------------------

SCORE_EXACT_PATH = 100.0
SCORE_EXACT_QUALIFIED_SYMBOL = 95.0
SCORE_EXACT_UNIQUE_SYMBOL = 90.0
SCORE_AMBIGUOUS_PATH = 85.0
SCORE_ACTIVE_DIFF_OVERLAP = 80.0
SCORE_AMBIGUOUS_SYMBOL = 70.0
SCORE_DIRECT_LEXICAL = 60.0
SCORE_DIRECT_STRUCTURAL = 50.0
SCORE_FOCUSED_TEST = 40.0
SCORE_WEAK_TEXTUAL = 20.0

CANDIDATE_KIND_FILE = "file"
CANDIDATE_KIND_SEMANTIC_UNIT = "semantic_unit"
CANDIDATE_KIND_EXACT_RANGE = "exact_range"
CANDIDATE_KIND_RELATIONSHIP_NEIGHBOR = "relationship_neighbor"
CANDIDATE_KIND_TEST = "test"
CANDIDATE_KIND_DIFF_PATH = "diff_path"


@dataclass(frozen=True, slots=True)
class SnapshotRef:
    """Explicit snapshot scope for candidate generation.

    Providers must not infer “latest” from timestamps; callers pass the id.
    """

    snapshot_id: int
    worktree_id: int
    worktree_root: Path
    state_timestamp: str | None = None
    commit_sha: str | None = None


@dataclass(frozen=True, slots=True)
class Candidate:
    """Likely useful retrieval target with internal ranking signals only."""

    path: str
    unit_id: int | None
    unit_version_id: int | None
    start_line: int | None
    end_line: int | None
    candidate_kind: str
    reasons: tuple[str, ...]
    provider: str
    raw_score: float | None
    metadata: Mapping[str, object] = field(default_factory=dict)


def candidate_identity(candidate: Candidate) -> tuple[Any, ...]:
    """Stable identity for dedupe within one snapshot.

    Prefer versioned unit identity, then logical unit + path, then exact
    range, then whole-file path.
    """
    if candidate.unit_version_id is not None:
        return ("unit_version", candidate.unit_version_id)
    if candidate.unit_id is not None:
        return ("unit", candidate.path, candidate.unit_id)
    if candidate.start_line is not None and candidate.end_line is not None:
        return ("range", candidate.path, candidate.start_line, candidate.end_line)
    return ("file", candidate.path)


def merge_candidates(existing: Candidate, incoming: Candidate) -> Candidate:
    """Merge two candidates that share :func:`candidate_identity`.

    Reasons are unioned (order-preserving), ``raw_score`` keeps the stronger
    value, and provider provenance is recorded in metadata.
    """
    reasons = tuple(dict.fromkeys((*existing.reasons, *incoming.reasons)))
    score: float | None
    if existing.raw_score is None:
        score = incoming.raw_score
    elif incoming.raw_score is None:
        score = existing.raw_score
    else:
        score = max(existing.raw_score, incoming.raw_score)

    providers: list[str] = []
    for value in (
        existing.metadata.get("providers"),
        (existing.provider,),
        incoming.metadata.get("providers"),
        (incoming.provider,),
    ):
        if isinstance(value, (list, tuple)):
            providers.extend(str(p) for p in value)
        elif value is not None and not isinstance(value, (list, tuple)):
            providers.append(str(value))
    provider_tuple = tuple(dict.fromkeys(providers)) or (existing.provider,)

    meta: dict[str, object] = dict(existing.metadata)
    meta.update({k: v for k, v in incoming.metadata.items() if k != "providers"})
    meta["providers"] = provider_tuple

    # Prefer the higher-scoring candidate's primary provider label.
    if incoming.raw_score is not None and (
        existing.raw_score is None or incoming.raw_score > existing.raw_score
    ):
        primary_provider = incoming.provider
        primary_kind = incoming.candidate_kind
        unit_id = incoming.unit_id if incoming.unit_id is not None else existing.unit_id
        unit_version_id = (
            incoming.unit_version_id
            if incoming.unit_version_id is not None
            else existing.unit_version_id
        )
        start_line = incoming.start_line if incoming.start_line is not None else existing.start_line
        end_line = incoming.end_line if incoming.end_line is not None else existing.end_line
    else:
        primary_provider = existing.provider
        primary_kind = existing.candidate_kind
        unit_id = existing.unit_id if existing.unit_id is not None else incoming.unit_id
        unit_version_id = (
            existing.unit_version_id
            if existing.unit_version_id is not None
            else incoming.unit_version_id
        )
        start_line = existing.start_line if existing.start_line is not None else incoming.start_line
        end_line = existing.end_line if existing.end_line is not None else incoming.end_line

    return Candidate(
        path=existing.path,
        unit_id=unit_id,
        unit_version_id=unit_version_id,
        start_line=start_line,
        end_line=end_line,
        candidate_kind=primary_kind,
        reasons=reasons,
        provider=primary_provider,
        raw_score=score,
        metadata=meta,
    )


def sort_key(candidate: Candidate) -> tuple[Any, ...]:
    """Deterministic ordering: score desc, then path/range/unit identity."""
    score = candidate.raw_score if candidate.raw_score is not None else float("-inf")
    return (
        -score,
        candidate.path,
        candidate.start_line if candidate.start_line is not None else -1,
        candidate.end_line if candidate.end_line is not None else -1,
        candidate.unit_version_id if candidate.unit_version_id is not None else -1,
        candidate.unit_id if candidate.unit_id is not None else -1,
        candidate.candidate_kind,
        candidate.provider,
    )


__all__ = [
    "CANDIDATE_KIND_DIFF_PATH",
    "CANDIDATE_KIND_EXACT_RANGE",
    "CANDIDATE_KIND_FILE",
    "CANDIDATE_KIND_RELATIONSHIP_NEIGHBOR",
    "CANDIDATE_KIND_SEMANTIC_UNIT",
    "CANDIDATE_KIND_TEST",
    "SCORE_ACTIVE_DIFF_OVERLAP",
    "SCORE_AMBIGUOUS_PATH",
    "SCORE_AMBIGUOUS_SYMBOL",
    "SCORE_DIRECT_LEXICAL",
    "SCORE_DIRECT_STRUCTURAL",
    "SCORE_EXACT_PATH",
    "SCORE_EXACT_QUALIFIED_SYMBOL",
    "SCORE_EXACT_UNIQUE_SYMBOL",
    "SCORE_FOCUSED_TEST",
    "SCORE_WEAK_TEXTUAL",
    "Candidate",
    "SnapshotRef",
    "candidate_identity",
    "merge_candidates",
    "sort_key",
]
