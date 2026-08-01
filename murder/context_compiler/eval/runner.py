"""Run evaluation cases against an indexed fixture worktree.

Deterministic: same case + same snapshot → byte-identical candidate keys /
corpus range keys / graded corpus fingerprints. No model calls.

Step 3 measures candidate recall. Step 4 adds corpus mode: top-k range recall,
token counts, profile comparison, and expansion distance. Step 5 adds graded
mode: fake-grader post-validation recall, forbidden hits, and determinism
after ``grade_corpus``.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from murder.context_compiler.candidates.composite import build_default_composite
from murder.context_compiler.candidates.models import Candidate, SnapshotRef
from murder.context_compiler.eval.cases import (
    EvalCase,
    EvalCaseReport,
    EvalReport,
    UnitRef,
)
from murder.context_compiler.eval.fixtures import all_candidate_cases, materialize_fixture_repo
from murder.context_compiler.grading.fakes import (
    FakeContextGrader,
    exclude_paths_grader,
    gaps_then_adequate_grader,
)
from murder.context_compiler.grading.grade import build_corpus_grader
from murder.context_compiler.grading.models import GradedCorpus, RequestDelta
from murder.context_compiler.indexing import index_worktree_sync
from murder.context_compiler.indexing.queries import list_semantic_units_by_path
from murder.context_compiler.models import ContextRequest, RepositoryState
from murder.context_compiler.persistence import open_context_index
from murder.context_compiler.ranking.models import CorpusProposal, RangeProposal
from murder.context_compiler.ranking.propose import build_corpus_proposer
from murder.context_compiler.ranking.trace import RankingTrace


@dataclass(frozen=True, slots=True)
class CandidateSnapshot:
    """Deterministic serialization of one candidate for equality checks."""

    path: str
    unit_name: str | None
    unit_id: int | None
    start_line: int | None
    end_line: int | None
    provider: str
    reasons: tuple[str, ...]

    def key(self) -> str:
        return json.dumps(
            {
                "path": self.path,
                "unit_name": self.unit_name,
                "unit_id": self.unit_id,
                "start_line": self.start_line,
                "end_line": self.end_line,
                "provider": self.provider,
                "reasons": list(self.reasons),
            },
            sort_keys=True,
            separators=(",", ":"),
        )


@dataclass(frozen=True, slots=True)
class RangeSnapshot:
    """Deterministic serialization of one corpus range."""

    path: str
    start_line: int
    end_line: int
    unit_name: str | None
    category: str
    score: float
    estimated_tokens: int
    reasons: tuple[str, ...]

    def key(self) -> str:
        return json.dumps(
            {
                "path": self.path,
                "start_line": self.start_line,
                "end_line": self.end_line,
                "unit_name": self.unit_name,
                "category": self.category,
                "score": self.score,
                "estimated_tokens": self.estimated_tokens,
                "reasons": list(self.reasons),
            },
            sort_keys=True,
            separators=(",", ":"),
        )


@dataclass(frozen=True, slots=True)
class GradedSnapshot:
    """Deterministic serialization of one graded corpus for equality checks."""

    path: str
    start_line: int
    end_line: int
    unit_name: str | None
    category: str
    score: float
    estimated_tokens: int
    reasons: tuple[str, ...]

    def key(self) -> str:
        return json.dumps(
            {
                "path": self.path,
                "start_line": self.start_line,
                "end_line": self.end_line,
                "unit_name": self.unit_name,
                "category": self.category,
                "score": self.score,
                "estimated_tokens": self.estimated_tokens,
                "reasons": list(self.reasons),
            },
            sort_keys=True,
            separators=(",", ":"),
        )


def graded_corpus_fingerprint(graded: GradedCorpus) -> str:
    """Byte-stable fingerprint of a full ``GradedCorpus`` (Step 4 spirit)."""
    payload = {
        "snapshot_id": graded.snapshot_id,
        "profile": graded.profile.value,
        "ranges": [
            {
                "path": r.path,
                "start_line": r.line_range.start_line,
                "end_line": r.line_range.end_line,
                "unit_version_id": r.unit_version_id,
                "category": r.category.value,
                "score": r.score,
                "reasons": list(r.reasons),
                "estimated_tokens": r.estimated_tokens,
            }
            for r in graded.ranges
        ],
        "grades": [
            {
                "proposal_index": g.proposal_index,
                "include": g.include,
                "category": g.category.value,
                "reason_code": g.reason_code.value,
            }
            for g in graded.grades
        ],
        "estimated_tokens": graded.estimated_tokens,
        "unresolved_questions": list(graded.unresolved_questions),
        "expansion_rounds": graded.expansion_rounds,
        "used_fallback": graded.used_fallback,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def unit_ref_from_candidate(
    conn: object,
    *,
    snapshot_id: int,
    candidate: Candidate,
) -> UnitRef | None:
    """Map a candidate to ``path::unqualified_name`` when a unit is known."""
    if candidate.unit_version_id is None and candidate.unit_id is None:
        return None
    units = list_semantic_units_by_path(
        conn,  # type: ignore[arg-type]
        snapshot_id=snapshot_id,
        relative_path=candidate.path,
    )
    for unit in units:
        if candidate.unit_version_id is not None:
            if unit.unit_version_id == candidate.unit_version_id:
                return UnitRef(candidate.path, unit.unqualified_name)
        elif candidate.unit_id is not None and unit.unit_id == candidate.unit_id:
            return UnitRef(candidate.path, unit.unqualified_name)
    if candidate.metadata.get("qualified_name"):
        qn = str(candidate.metadata["qualified_name"])
        name = qn.rsplit(".", 1)[-1]
        return UnitRef(candidate.path, name)
    return None


def unit_ref_from_range(
    conn: object,
    *,
    snapshot_id: int,
    proposal: RangeProposal,
) -> UnitRef | None:
    units = list_semantic_units_by_path(
        conn,  # type: ignore[arg-type]
        snapshot_id=snapshot_id,
        relative_path=proposal.path,
    )
    if proposal.unit_version_id is not None:
        for unit in units:
            if unit.unit_version_id == proposal.unit_version_id:
                return UnitRef(proposal.path, unit.unqualified_name)
    # Overlap: smallest containing unit for the range start.
    start = proposal.line_range.start_line
    containing = [u for u in units if u.start_line <= start <= u.end_line]
    if containing:
        containing.sort(key=lambda u: (u.end_line - u.start_line, u.start_line))
        return UnitRef(proposal.path, containing[0].unqualified_name)
    return None


def _candidate_snapshot(
    conn: object,
    *,
    snapshot_id: int,
    candidate: Candidate,
) -> CandidateSnapshot:
    ref = unit_ref_from_candidate(conn, snapshot_id=snapshot_id, candidate=candidate)
    return CandidateSnapshot(
        path=candidate.path,
        unit_name=ref.unit if ref else None,
        unit_id=candidate.unit_id,
        start_line=candidate.start_line,
        end_line=candidate.end_line,
        provider=candidate.provider,
        reasons=tuple(candidate.reasons),
    )


def _range_snapshot(
    conn: object,
    *,
    snapshot_id: int,
    proposal: RangeProposal,
) -> RangeSnapshot:
    ref = unit_ref_from_range(conn, snapshot_id=snapshot_id, proposal=proposal)
    return RangeSnapshot(
        path=proposal.path,
        start_line=proposal.line_range.start_line,
        end_line=proposal.line_range.end_line,
        unit_name=ref.unit if ref else None,
        category=proposal.category.value,
        score=proposal.score,
        estimated_tokens=proposal.estimated_tokens,
        reasons=tuple(proposal.reasons),
    )


def _score_case(
    case: EvalCase,
    *,
    snapshots_a: tuple[CandidateSnapshot, ...],
    snapshots_b: tuple[CandidateSnapshot, ...],
) -> EvalCaseReport:
    keys_a = tuple(s.key() for s in snapshots_a)
    keys_b = tuple(s.key() for s in snapshots_b)
    determinism = "identical" if keys_a == keys_b else "diverged"

    hit_expected: list[str] = []
    missed_expected: list[str] = []
    for exp in case.expected:
        if any(s.path == exp.path and s.unit_name == exp.unit for s in snapshots_a):
            hit_expected.append(exp.key())
        else:
            missed_expected.append(exp.key())

    top = snapshots_a[: case.top_k]
    top_hit = sum(
        1
        for exp in case.expected
        if any(s.path == exp.path and s.unit_name == exp.unit for s in top)
    )
    expected_recall = len(hit_expected) / len(case.expected) if case.expected else 1.0
    top_k_recall = top_hit / len(case.expected) if case.expected else 1.0

    hit_forbidden = [
        exp.key()
        for exp in case.forbidden
        if any(s.path == exp.path and s.unit_name == exp.unit for s in snapshots_a)
    ]
    providers = tuple(sorted({s.provider for s in snapshots_a}))

    return EvalCaseReport(
        name=case.name,
        candidate_count=len(snapshots_a),
        expected_unit_recall=expected_recall,
        top_k_recall=top_k_recall,
        forbidden_unit_hits=len(hit_forbidden),
        provider_attribution=providers,
        determinism_status=determinism,
        hit_expected=tuple(hit_expected),
        missed_expected=tuple(missed_expected),
        hit_forbidden=tuple(hit_forbidden),
    )


def _score_corpus_case(
    case: EvalCase,
    *,
    ranges_a: tuple[RangeSnapshot, ...],
    ranges_b: tuple[RangeSnapshot, ...],
    proposal: CorpusProposal,
    max_expansion_distance: int,
) -> EvalCaseReport:
    keys_a = tuple(s.key() for s in ranges_a)
    keys_b = tuple(s.key() for s in ranges_b)
    determinism = "identical" if keys_a == keys_b else "diverged"

    hit_expected: list[str] = []
    missed_expected: list[str] = []
    for exp in case.expected:
        if any(s.path == exp.path and s.unit_name == exp.unit for s in ranges_a):
            hit_expected.append(exp.key())
        else:
            missed_expected.append(exp.key())

    top = ranges_a[: case.top_k]
    top_hit = sum(
        1
        for exp in case.expected
        if any(s.path == exp.path and s.unit_name == exp.unit for s in top)
    )
    expected_recall = len(hit_expected) / len(case.expected) if case.expected else 1.0
    top_k_recall = top_hit / len(case.expected) if case.expected else 1.0

    hit_forbidden = [
        exp.key()
        for exp in case.forbidden
        if any(s.path == exp.path and s.unit_name == exp.unit for s in ranges_a)
    ]

    hit_ranges: list[str] = []
    missed_ranges: list[str] = []
    for range_exp in case.expected_ranges:
        if any(
            range_exp.overlaps(s.path, s.start_line, s.end_line) for s in ranges_a[: case.top_k]
        ):
            hit_ranges.append(range_exp.key())
        else:
            missed_ranges.append(range_exp.key())
    range_recall = len(hit_ranges) / len(case.expected_ranges) if case.expected_ranges else 1.0

    hit_forbidden_ranges = [
        range_exp.key()
        for range_exp in case.forbidden_ranges
        if any(range_exp.overlaps(s.path, s.start_line, s.end_line) for s in ranges_a)
    ]

    return EvalCaseReport(
        name=case.name,
        candidate_count=len(ranges_a),
        expected_unit_recall=expected_recall,
        top_k_recall=top_k_recall,
        forbidden_unit_hits=len(hit_forbidden) + len(hit_forbidden_ranges),
        provider_attribution=(),
        determinism_status=determinism,
        hit_expected=tuple(hit_expected),
        missed_expected=tuple(missed_expected),
        hit_forbidden=tuple(hit_forbidden),
        estimated_tokens=proposal.estimated_tokens,
        top_k_range_recall=range_recall,
        max_expansion_distance=max_expansion_distance,
        truncated=proposal.truncated,
        hit_expected_ranges=tuple(hit_ranges),
        missed_expected_ranges=tuple(missed_ranges),
        hit_forbidden_ranges=tuple(hit_forbidden_ranges),
    )


def _score_graded_case(
    case: EvalCase,
    *,
    snaps_a: tuple[GradedSnapshot, ...],
    snaps_b: tuple[GradedSnapshot, ...],
    fingerprint_a: str,
    fingerprint_b: str,
    graded: GradedCorpus,
) -> EvalCaseReport:
    """Score recall/forbidden after grading; determinism via fingerprint + keys."""
    keys_a = tuple(s.key() for s in snaps_a)
    keys_b = tuple(s.key() for s in snaps_b)
    determinism = "identical" if keys_a == keys_b and fingerprint_a == fingerprint_b else "diverged"

    hit_expected: list[str] = []
    missed_expected: list[str] = []
    for exp in case.expected:
        if any(s.path == exp.path and s.unit_name == exp.unit for s in snaps_a):
            hit_expected.append(exp.key())
        else:
            missed_expected.append(exp.key())

    top = snaps_a[: case.top_k]
    top_hit = sum(
        1
        for exp in case.expected
        if any(s.path == exp.path and s.unit_name == exp.unit for s in top)
    )
    expected_recall = len(hit_expected) / len(case.expected) if case.expected else 1.0
    top_k_recall = top_hit / len(case.expected) if case.expected else 1.0

    hit_forbidden = [
        exp.key()
        for exp in case.forbidden
        if any(s.path == exp.path and s.unit_name == exp.unit for s in snaps_a)
    ]

    expansion_ok: bool | None = None
    if case.expect_expansion_rounds is not None:
        expansion_ok = graded.expansion_rounds == case.expect_expansion_rounds

    return EvalCaseReport(
        name=case.name,
        candidate_count=len(snaps_a),
        expected_unit_recall=expected_recall,
        top_k_recall=top_k_recall,
        forbidden_unit_hits=len(hit_forbidden),
        provider_attribution=(),
        determinism_status=determinism,
        hit_expected=tuple(hit_expected),
        missed_expected=tuple(missed_expected),
        hit_forbidden=tuple(hit_forbidden),
        estimated_tokens=graded.estimated_tokens,
        truncated=False,
        expansion_rounds=graded.expansion_rounds,
        used_fallback=graded.used_fallback,
        expansion_rounds_ok=expansion_ok,
    )


def _graded_snapshot(
    conn: object,
    *,
    snapshot_id: int,
    proposal: RangeProposal,
) -> GradedSnapshot:
    ref = unit_ref_from_range(conn, snapshot_id=snapshot_id, proposal=proposal)
    return GradedSnapshot(
        path=proposal.path,
        start_line=proposal.line_range.start_line,
        end_line=proposal.line_range.end_line,
        unit_name=ref.unit if ref else None,
        category=proposal.category.value,
        score=proposal.score,
        estimated_tokens=proposal.estimated_tokens,
        reasons=tuple(proposal.reasons),
    )


def _fake_grader_for_case(case: EvalCase) -> FakeContextGrader:
    """Build a hermetic fake grader from the case recipe fields."""
    if case.grader_exclude_paths:
        return exclude_paths_grader(*case.grader_exclude_paths)
    delta_terms = case.grader_gap_search_terms
    delta_kinds = case.grader_gap_relationship_kinds
    if delta_terms or delta_kinds:
        return gaps_then_adequate_grader(
            RequestDelta(
                search_terms=delta_terms,
                relationship_kinds=delta_kinds,
            )
        )
    return FakeContextGrader()


async def _grade_corpus(
    conn: object,
    *,
    case: EvalCase,
    snapshot: SnapshotRef,
    worktree_root: Path,
    repository_root: Path,
) -> GradedCorpus:
    """Propose then grade once — fake grader, real Step 4 proposer."""
    proposer = build_corpus_proposer(conn, worktree_root=worktree_root)  # type: ignore[arg-type]
    request = ContextRequest(
        request_id=f"eval:{case.name}",
        recipient_id="eval-harness",
        repository_state=RepositoryState(
            repository_root=repository_root,
            worktree_root=worktree_root,
            state_timestamp=datetime.now(timezone.utc),
            commit_sha=None,
        ),
        objective=case.objective,
        recipient_profile=case.profile,
        path_hints=case.path_hints,
        symbol_hints=case.symbol_hints,
        max_tokens=case.max_tokens,
    )
    initial = await proposer.propose_corpus(request, snapshot)
    grader = _fake_grader_for_case(case)
    corpus_grader = build_corpus_grader(
        grader,
        worktree_root=worktree_root,
        conn=conn,  # type: ignore[arg-type]
        proposer=proposer,
    )
    return await corpus_grader.grade_corpus(request, initial, snapshot)


async def _generate_candidates(
    conn: object,
    *,
    case: EvalCase,
    snapshot: SnapshotRef,
    worktree_root: Path,
    repository_root: Path,
) -> tuple[Candidate, ...]:
    provider = build_default_composite(conn, worktree_root=worktree_root)
    request = ContextRequest(
        request_id=f"eval:{case.name}",
        recipient_id="eval-harness",
        repository_state=RepositoryState(
            repository_root=repository_root,
            worktree_root=worktree_root,
            state_timestamp=datetime.now(timezone.utc),
            commit_sha=None,
        ),
        objective=case.objective,
        recipient_profile=case.profile,
        path_hints=case.path_hints,
        symbol_hints=case.symbol_hints,
    )
    result = await provider.generate(request, snapshot, ())
    return tuple(result)


async def _propose_corpus(
    conn: object,
    *,
    case: EvalCase,
    snapshot: SnapshotRef,
    worktree_root: Path,
    repository_root: Path,
) -> tuple[CorpusProposal, RankingTrace]:
    proposer = build_corpus_proposer(conn, worktree_root=worktree_root)  # type: ignore[arg-type]
    request = ContextRequest(
        request_id=f"eval:{case.name}",
        recipient_id="eval-harness",
        repository_state=RepositoryState(
            repository_root=repository_root,
            worktree_root=worktree_root,
            state_timestamp=datetime.now(timezone.utc),
            commit_sha=None,
        ),
        objective=case.objective,
        recipient_profile=case.profile,
        path_hints=case.path_hints,
        symbol_hints=case.symbol_hints,
        max_tokens=case.max_tokens,
    )
    trace = RankingTrace()
    proposal = await proposer.propose_corpus(request, snapshot, trace=trace)
    return proposal, trace


def _max_expansion_distance(trace: RankingTrace) -> int:
    dist = 0
    for event in trace.events:
        if event.kind == "scored" and event.reason_code.startswith("expanded_hop_"):
            try:
                dist = max(dist, int(event.reason_code.rsplit("_", 1)[-1]))
            except ValueError:
                continue
    return dist


def run_case(
    case: EvalCase,
    *,
    work_dir: Path,
) -> EvalCaseReport:
    """Index the fixture, run the case twice, and score recall / corpus metrics."""
    repo = work_dir
    wt = repo / "wt"
    if repo.exists():
        shutil.rmtree(repo)
    wt.mkdir(parents=True)
    materialize_fixture_repo(case.fixture_shape, wt)

    conn = open_context_index(repo, db_path=repo / ".murder" / "context-index.db")
    try:
        result = index_worktree_sync(
            repository_root=repo,
            worktree_root=wt,
            state_timestamp="2026-08-01T00:00:00Z",
            commit_sha=None,
            conn=conn,
        )
        if result.status != "ready" or result.snapshot_id < 0:
            raise RuntimeError(
                f"indexing failed for {case.name}: status={result.status} "
                f"reason={result.failure_reason}"
            )

        snapshot = SnapshotRef(
            snapshot_id=result.snapshot_id,
            worktree_id=result.worktree_id,
            worktree_root=wt,
            state_timestamp=result.state_timestamp,
            commit_sha=None,
        )

        if case.mode == "corpus":

            async def _twice_corpus() -> tuple[
                CorpusProposal,
                CorpusProposal,
                RankingTrace,
            ]:
                a, ta = await _propose_corpus(
                    conn,
                    case=case,
                    snapshot=snapshot,
                    worktree_root=wt,
                    repository_root=repo,
                )
                b, _tb = await _propose_corpus(
                    conn,
                    case=case,
                    snapshot=snapshot,
                    worktree_root=wt,
                    repository_root=repo,
                )
                return a, b, ta

            prop_a, prop_b, trace = asyncio.run(_twice_corpus())
            range_snaps_a = tuple(
                _range_snapshot(conn, snapshot_id=snapshot.snapshot_id, proposal=r)
                for r in prop_a.ranges
            )
            range_snaps_b = tuple(
                _range_snapshot(conn, snapshot_id=snapshot.snapshot_id, proposal=r)
                for r in prop_b.ranges
            )
            return _score_corpus_case(
                case,
                ranges_a=range_snaps_a,
                ranges_b=range_snaps_b,
                proposal=prop_a,
                max_expansion_distance=_max_expansion_distance(trace),
            )

        if case.mode == "graded":

            async def _twice_graded() -> tuple[GradedCorpus, GradedCorpus]:
                a = await _grade_corpus(
                    conn,
                    case=case,
                    snapshot=snapshot,
                    worktree_root=wt,
                    repository_root=repo,
                )
                b = await _grade_corpus(
                    conn,
                    case=case,
                    snapshot=snapshot,
                    worktree_root=wt,
                    repository_root=repo,
                )
                return a, b

            graded_a, graded_b = asyncio.run(_twice_graded())
            snaps_a = tuple(
                _graded_snapshot(conn, snapshot_id=snapshot.snapshot_id, proposal=r)
                for r in graded_a.ranges
            )
            snaps_b = tuple(
                _graded_snapshot(conn, snapshot_id=snapshot.snapshot_id, proposal=r)
                for r in graded_b.ranges
            )
            return _score_graded_case(
                case,
                snaps_a=snaps_a,
                snaps_b=snaps_b,
                fingerprint_a=graded_corpus_fingerprint(graded_a),
                fingerprint_b=graded_corpus_fingerprint(graded_b),
                graded=graded_a,
            )

        async def _twice() -> tuple[tuple[Candidate, ...], tuple[Candidate, ...]]:
            a = await _generate_candidates(
                conn,
                case=case,
                snapshot=snapshot,
                worktree_root=wt,
                repository_root=repo,
            )
            b = await _generate_candidates(
                conn,
                case=case,
                snapshot=snapshot,
                worktree_root=wt,
                repository_root=repo,
            )
            return a, b

        cand_a, cand_b = asyncio.run(_twice())
        cand_snaps_a = tuple(
            _candidate_snapshot(conn, snapshot_id=snapshot.snapshot_id, candidate=c) for c in cand_a
        )
        cand_snaps_b = tuple(
            _candidate_snapshot(conn, snapshot_id=snapshot.snapshot_id, candidate=c) for c in cand_b
        )
        return _score_case(case, snapshots_a=cand_snaps_a, snapshots_b=cand_snaps_b)
    finally:
        conn.close()


def _apply_token_comparisons(
    cases: tuple[EvalCase, ...],
    reports: tuple[EvalCaseReport, ...],
) -> tuple[EvalCaseReport, ...]:
    """Enforce ``expect_fewer_tokens_than`` across the report set.

    A case that declares a comparison fails when the named peer is missing or
    when its ``estimated_tokens`` is not strictly smaller.
    """
    by_name = {r.name: r for r in reports}
    case_by_name = {c.name: c for c in cases}
    updated: list[EvalCaseReport] = []
    for report in reports:
        case = case_by_name[report.name]
        if not case.expect_fewer_tokens_than:
            updated.append(report)
            continue
        peer = by_name.get(case.expect_fewer_tokens_than)
        ok = peer is not None and report.estimated_tokens < peer.estimated_tokens
        updated.append(replace(report, fewer_tokens_than_ok=ok))
    return tuple(updated)


def run_cases(
    cases: tuple[EvalCase, ...] | None = None,
    *,
    work_dir: Path,
) -> EvalReport:
    """Run evaluation cases; default is Step 3 candidate-mode cases.

    Pass ``all_corpus_cases()`` for Step 4, ``all_graded_cases()`` for Step 5.
    ``all_builtin_cases()`` still mixes modes when a full suite is wanted.
    """
    selected = cases if cases is not None else all_candidate_cases()
    reports = tuple(run_case(case, work_dir=work_dir / case.name) for case in selected)
    reports = _apply_token_comparisons(selected, reports)
    return EvalReport(cases=reports)


__all__ = [
    "CandidateSnapshot",
    "GradedSnapshot",
    "RangeSnapshot",
    "graded_corpus_fingerprint",
    "run_case",
    "run_cases",
    "unit_ref_from_candidate",
    "unit_ref_from_range",
]
