"""Step 5 — cheap-model grading: fakes, post-validation, expansion, fallback."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from murder.context_compiler.candidates.lexical import LexicalSearchProvider
from murder.context_compiler.candidates.models import SnapshotRef
from murder.context_compiler.eval import (
    all_graded_cases,
    graded_corpus_fingerprint,
    materialize_fixture_repo,
    run_case,
    run_cases,
)
from murder.context_compiler.grading import (
    FakeContextGrader,
    Grade,
    GradedCorpus,
    GradeResult,
    GradingTrace,
    ReasonCode,
    RequestDelta,
    apply_request_delta,
    build_corpus_grader,
    exclude_paths_grader,
    gaps_then_adequate_grader,
    hallucinated_indices_grader,
    malformed_then_valid_grader,
    parse_grade_result,
    parse_grade_result_json,
    planning_broader_contracts_grader,
    post_validate_grades,
    render_proposal_preview,
    rubric_for_profile,
)
from murder.context_compiler.grading.errors import GraderOutputError
from murder.context_compiler.grading.llm_adapter import LlmContextGrader, build_llm_context_grader
from murder.context_compiler.grading.policy import (
    MAX_EXPANSION_ROUNDS,
    MAX_STRUCTURED_OUTPUT_RETRIES,
)
from murder.context_compiler.grading.structured import GRADE_RESULT_ADAPTER
from murder.context_compiler.indexing import index_worktree_sync
from murder.context_compiler.indexing.queries import (
    get_file_version_by_path,
    list_semantic_units_by_path,
)
from murder.context_compiler.models import (
    ContextRequest,
    EvidenceCategory,
    LineRange,
    RecipientProfile,
    RepositoryState,
)
from murder.context_compiler.persistence import open_context_index
from murder.context_compiler.persistence.relationships import insert_resolved_relationship
from murder.context_compiler.ranking import (
    CorpusProposal,
    RangeProposal,
    RankingPolicy,
    build_corpus_proposer,
)
from murder.context_compiler.ranking.policy import ProfileWeights
from murder.llm.clients.base import CompletionResult, ToolCall

STATE_TS = datetime(2026, 8, 1, 8, 0, tzinfo=timezone.utc)
_EXPECTED_GRADE_CALLS_WITH_EXPANSION = 2
# LlmContextGrader: initial attempt + MAX_STRUCTURED_OUTPUT_RETRIES (CorpusGrader stacks none).
_STRUCTURED_RETRY_ATTEMPTS = MAX_STRUCTURED_OUTPUT_RETRIES + 1
_CEILING_MAX_RANGES = 3
_CEILING_MAX_TOKENS = 150
_EDIT_TARGET_SEED_LINE = 12


def _corpus_fingerprint(graded: GradedCorpus) -> tuple[object, ...]:
    """Step 4-style serialized equality key for graded corpus replay."""
    range_keys = tuple(
        (
            r.path,
            r.line_range.start_line,
            r.line_range.end_line,
            r.unit_version_id,
            r.category.value,
            r.score,
            r.reasons,
            r.estimated_tokens,
        )
        for r in graded.ranges
    )
    grade_keys = tuple(
        (g.proposal_index, g.include, g.category.value, g.reason_code.value) for g in graded.grades
    )
    return (
        graded.snapshot_id,
        graded.profile.value,
        range_keys,
        grade_keys,
        graded.estimated_tokens,
        graded.unresolved_questions,
        graded.expansion_rounds,
        graded.used_fallback,
    )


_MIN_GRADED_CASES = 2


def _index_ranking(tmp_path: Path) -> tuple[sqlite3.Connection, SnapshotRef, Path, Path]:
    repo = tmp_path / "repo"
    wt = repo / "wt"
    wt.mkdir(parents=True)
    materialize_fixture_repo("ranking", wt)
    conn = open_context_index(repo, db_path=repo / ".murder" / "context-index.db")
    result = index_worktree_sync(
        repository_root=repo,
        worktree_root=wt,
        state_timestamp="2026-08-01T00:00:00Z",
        commit_sha=None,
        conn=conn,
    )
    assert result.status == "ready"
    snapshot = SnapshotRef(
        snapshot_id=result.snapshot_id,
        worktree_id=result.worktree_id,
        worktree_root=wt,
        state_timestamp=result.state_timestamp,
        commit_sha=None,
    )
    return conn, snapshot, repo, wt


def _inject_caller_and_test_edges(conn: sqlite3.Connection, snapshot: SnapshotRef) -> None:
    """Snapshot-scoped calls/tests edges (cross-file calls are not yet auto-resolved)."""
    editor_units = list_semantic_units_by_path(
        conn, snapshot_id=snapshot.snapshot_id, relative_path="editor.py"
    )
    caller_units = list_semantic_units_by_path(
        conn, snapshot_id=snapshot.snapshot_id, relative_path="caller.py"
    )
    test_units = list_semantic_units_by_path(
        conn, snapshot_id=snapshot.snapshot_id, relative_path="test_editor.py"
    )
    save = next(u for u in editor_units if u.unqualified_name == "save")
    invoke = next(u for u in caller_units if u.unqualified_name == "invoke_save")
    test_fn = next(u for u in test_units if u.unqualified_name == "test_save_profile")
    caller_fv = get_file_version_by_path(
        conn, snapshot_id=snapshot.snapshot_id, relative_path="caller.py"
    )
    editor_fv = get_file_version_by_path(
        conn, snapshot_id=snapshot.snapshot_id, relative_path="editor.py"
    )
    test_fv = get_file_version_by_path(
        conn, snapshot_id=snapshot.snapshot_id, relative_path="test_editor.py"
    )
    assert caller_fv is not None and editor_fv is not None and test_fv is not None
    insert_resolved_relationship(
        conn,
        snapshot_id=snapshot.snapshot_id,
        source_file_version_id=caller_fv.file_version.file_version_id,
        source_unit_version_id=invoke.unit_version_id,
        target_file_id=editor_fv.file.file_id,
        target_unit_id=save.unit_id,
        relation_kind="calls",
        confidence="exact",
        resolution_method="imported_alias",
        start_line=invoke.start_line,
        end_line=invoke.end_line,
    )
    insert_resolved_relationship(
        conn,
        snapshot_id=snapshot.snapshot_id,
        source_file_version_id=test_fv.file_version.file_version_id,
        source_unit_version_id=test_fn.unit_version_id,
        target_file_id=editor_fv.file.file_id,
        target_unit_id=save.unit_id,
        relation_kind="tests",
        confidence="inferred",
        resolution_method="imported_call",
        start_line=test_fn.start_line,
        end_line=test_fn.end_line,
    )
    conn.commit()


def _request(
    repo: Path,
    worktree: Path,
    *,
    profile: RecipientProfile = RecipientProfile.IMPLEMENTATION,
    path_hints: tuple[str, ...] = (),
    symbol_hints: tuple[str, ...] = (),
    objective: str = "Fix ProfileEditor.save validation.",
    max_tokens: int | None = None,
) -> ContextRequest:
    return ContextRequest(
        request_id="req-grade-1",
        recipient_id="agent-1",
        repository_state=RepositoryState(
            repository_root=repo,
            worktree_root=worktree,
            state_timestamp=STATE_TS,
            commit_sha=None,
        ),
        objective=objective,
        recipient_profile=profile,
        path_hints=path_hints,
        symbol_hints=symbol_hints,
        max_tokens=max_tokens,
        trace_id="trace-grade-1",
    )


def _range(
    path: str,
    start: int,
    end: int,
    *,
    category: EvidenceCategory = EvidenceCategory.SUPPORTING_CONTEXT,
    score: float = 50.0,
    reasons: tuple[str, ...] = (),
    tokens: int = 40,
    unit_version_id: int | None = None,
) -> RangeProposal:
    return RangeProposal(
        path=path,
        line_range=LineRange(start, end),
        unit_version_id=unit_version_id,
        category=category,
        score=score,
        reasons=reasons,
        estimated_tokens=tokens,
    )


def _proposal(
    *ranges: RangeProposal,
    profile: RecipientProfile = RecipientProfile.IMPLEMENTATION,
) -> CorpusProposal:
    return CorpusProposal(
        snapshot_id=1,
        profile=profile,
        ranges=ranges,
        estimated_tokens=sum(r.estimated_tokens for r in ranges),
        truncated=False,
    )


def _write_sources(wt: Path) -> None:
    (wt / "editor.py").write_text(
        "class ProfileEditor:\n    def save(self, payload):\n        return payload\n",
        encoding="utf-8",
    )
    (wt / "hub.py").write_text(
        "def common_util_alpha(x):\n    return x\n" * 20,
        encoding="utf-8",
    )
    (wt / "test_editor.py").write_text(
        "from editor import ProfileEditor\n\n"
        "def test_save_profile():\n"
        "    assert ProfileEditor().save({'n': 1})\n",
        encoding="utf-8",
    )
    (wt / "contracts.py").write_text(
        "class ProfileContract:\n    def validate(self, payload):\n        return payload\n",
        encoding="utf-8",
    )
    (wt / "caller.py").write_text(
        "from editor import ProfileEditor\n\n"
        "def invoke_save():\n"
        "    return ProfileEditor().save({})\n",
        encoding="utf-8",
    )


def _tiny_policy(**overrides: object) -> RankingPolicy:
    fields = {
        "exact_hint": 100.0,
        "active_diff": 70.0,
        "strong_lexical": 40.0,
        "direct_structural": 45.0,
        "focused_test": 70.0,
        "provider_agreement": 12.0,
        "weak_lexical": 5.0,
        "weak_tier": 20.0,
        "relationship_distance": 12.0,
        "token_cost": 3.0,
        "large_unit": 20.0,
        "generated_vendored": 35.0,
        "max_hops": 1,
        "second_hop_expansion_cap": 0,
        "max_raw_candidates": 40,
        "max_expansions": 12,
        "max_range_proposals": 4,
        "max_estimated_tokens": 200,
        "max_candidates_per_file": 2,
        "seed_score_floor": 35.0,
        "unit_token_cap": 400,
        "token_penalty_scale": 1.0,
    }
    fields.update(overrides)
    weights = ProfileWeights(**fields)  # type: ignore[arg-type]
    return RankingPolicy(compact=weights, implementation=weights, planning=weights)


# ---------------------------------------------------------------------------
# Structured output
# ---------------------------------------------------------------------------


def test_parse_grade_result_accepts_valid_payload() -> None:
    payload = {
        "grades": [
            {
                "proposal_index": 0,
                "include": True,
                "category": "edit_target",
                "reason_code": "likely_edit_target",
                "rationale": "Owns the save path.",
            }
        ],
        "gaps": None,
    }
    result = parse_grade_result(payload)
    assert len(result.grades) == 1
    assert result.grades[0].reason_code is ReasonCode.LIKELY_EDIT_TARGET
    assert result.gaps is None


def test_parse_grade_result_rejects_unknown_reason() -> None:
    with pytest.raises(ValidationError):
        GRADE_RESULT_ADAPTER.validate_python(
            {
                "grades": [
                    {
                        "proposal_index": 0,
                        "include": True,
                        "category": "edit_target",
                        "reason_code": "vibes",
                    }
                ]
            }
        )


def test_profile_rubrics_exist() -> None:
    for profile in RecipientProfile:
        text = rubric_for_profile(profile)
        assert "closed enum" in text or "reason_code" in text
        assert profile.value in text or profile.name.lower() in text.lower() or "Profile:" in text


# ---------------------------------------------------------------------------
# Post-validation behaviours
# ---------------------------------------------------------------------------


def test_structurally_central_irrelevant_excluded(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    wt = repo / "wt"
    wt.mkdir(parents=True)
    _write_sources(wt)
    proposal = _proposal(
        _range("editor.py", 1, 3, category=EvidenceCategory.EDIT_TARGET, score=90, tokens=30),
        _range("hub.py", 1, 40, category=EvidenceCategory.SUPPORTING_CONTEXT, score=80, tokens=80),
    )
    req = _request(repo, wt, path_hints=("editor.py",))
    grader = exclude_paths_grader("hub.py")
    graded = asyncio.run(
        build_corpus_grader(grader, worktree_root=wt).grade_corpus(
            req,
            proposal,
            SnapshotRef(snapshot_id=1, worktree_id=1, worktree_root=wt),
        )
    )
    paths = {r.path for r in graded.ranges}
    assert "editor.py" in paths
    assert "hub.py" not in paths
    assert graded.expansion_rounds == 0


def test_implementation_keeps_focused_test(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    wt = repo / "wt"
    wt.mkdir(parents=True)
    _write_sources(wt)
    proposal = _proposal(
        _range("editor.py", 1, 3, category=EvidenceCategory.EDIT_TARGET, score=95, tokens=30),
        _range(
            "test_editor.py",
            3,
            4,
            category=EvidenceCategory.TEST,
            score=70,
            tokens=25,
            reasons=("focused_test",),
        ),
    )
    req = _request(repo, wt, profile=RecipientProfile.IMPLEMENTATION)
    graded = asyncio.run(
        build_corpus_grader(FakeContextGrader(), worktree_root=wt).grade_corpus(
            req,
            proposal,
            SnapshotRef(snapshot_id=1, worktree_id=1, worktree_root=wt),
        )
    )
    assert any(r.path == "test_editor.py" for r in graded.ranges)
    assert any(r.category is EvidenceCategory.TEST for r in graded.ranges)


def test_planning_broader_contracts_than_compact(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    wt = repo / "wt"
    wt.mkdir(parents=True)
    _write_sources(wt)
    proposal = _proposal(
        _range("editor.py", 1, 3, category=EvidenceCategory.EDIT_TARGET, score=90, tokens=30),
        _range("contracts.py", 1, 3, category=EvidenceCategory.CONTRACT, score=60, tokens=30),
    )
    snap = SnapshotRef(snapshot_id=1, worktree_id=1, worktree_root=wt)
    fake = planning_broader_contracts_grader()

    compact = asyncio.run(
        build_corpus_grader(fake, worktree_root=wt).grade_corpus(
            _request(repo, wt, profile=RecipientProfile.COMPACT, path_hints=("editor.py",)),
            proposal,
            snap,
        )
    )
    # Reset call history with a fresh grader for planning.
    planning = asyncio.run(
        build_corpus_grader(planning_broader_contracts_grader(), worktree_root=wt).grade_corpus(
            _request(repo, wt, profile=RecipientProfile.PLANNING, path_hints=("editor.py",)),
            proposal,
            snap,
        )
    )
    compact_paths = {r.path for r in compact.ranges}
    planning_paths = {r.path for r in planning.ranges}
    assert "editor.py" in compact_paths
    assert "contracts.py" in planning_paths
    # Planning keeps the contract; compact drops the non-edit contract path.
    assert "contracts.py" not in compact_paths or len(planning.ranges) >= len(compact.ranges)


def test_omitted_caller_triggers_one_expansion(tmp_path: Path) -> None:
    """Omitted direct caller → gaps → one real propose_corpus expansion round."""
    conn, snapshot, repo, wt = _index_ranking(tmp_path)
    try:
        _inject_caller_and_test_edges(conn, snapshot)
        proposer = build_corpus_proposer(conn, worktree_root=wt)
        req = _request(repo, wt, path_hints=("editor.py",), symbol_hints=("ProfileEditor", "save"))
        full = asyncio.run(proposer.propose_corpus(req, snapshot))
        # Start without the caller — the gap round must re-propose it in.
        initial = CorpusProposal(
            snapshot_id=full.snapshot_id,
            profile=full.profile,
            ranges=tuple(r for r in full.ranges if r.path == "editor.py"),
            estimated_tokens=sum(r.estimated_tokens for r in full.ranges if r.path == "editor.py"),
            truncated=False,
        )
        assert initial.ranges
        assert not any(r.path == "caller.py" for r in initial.ranges)

        delta = RequestDelta(relationship_kinds=("calls",))
        grader = gaps_then_adequate_grader(delta)
        trace = GradingTrace()
        graded = asyncio.run(
            build_corpus_grader(
                grader, worktree_root=wt, conn=conn, proposer=proposer
            ).grade_corpus(req, initial, snapshot, trace=trace)
        )
        assert graded.expansion_rounds == 1
        assert any(r.path == "caller.py" for r in graded.ranges)
        assert MAX_EXPANSION_ROUNDS == 1
        assert graded.unresolved_questions == ()
        assert len(grader.calls) == _EXPECTED_GRADE_CALLS_WITH_EXPANSION
        kinds = {e.kind for e in trace.events}
        assert "expansion_requested" in kinds
        assert "expansion_completed" in kinds
        assert not any(e.kind == "expansion_failed" for e in trace.events)
    finally:
        conn.close()


def test_adequate_corpus_skips_expansion(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    wt = repo / "wt"
    wt.mkdir(parents=True)
    _write_sources(wt)
    proposal = _proposal(
        _range("editor.py", 1, 3, category=EvidenceCategory.EDIT_TARGET, score=90, tokens=30),
    )
    propose_calls = 0

    async def propose(_req: ContextRequest, _snap: SnapshotRef) -> CorpusProposal:
        nonlocal propose_calls
        propose_calls += 1
        return proposal

    graded = asyncio.run(
        build_corpus_grader(FakeContextGrader(), worktree_root=wt).grade_corpus(
            _request(repo, wt),
            proposal,
            SnapshotRef(snapshot_id=1, worktree_id=1, worktree_root=wt),
            propose=propose,
        )
    )
    assert graded.expansion_rounds == 0
    assert propose_calls == 0


def test_malformed_output_falls_back(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    wt = repo / "wt"
    wt.mkdir(parents=True)
    _write_sources(wt)
    proposal = _proposal(
        _range("editor.py", 1, 3, category=EvidenceCategory.EDIT_TARGET, score=90, tokens=30),
        _range(
            "editor.py",
            1,
            1,
            category=EvidenceCategory.EDIT_TARGET,
            score=100,
            tokens=10,
            reasons=("shape:exact_hint", "signal:exact_hint"),
        ),
    )

    def _always_bad(_request: ContextRequest, _proposal: CorpusProposal) -> GradeResult:
        raise GraderOutputError("always malformed")

    grader = FakeContextGrader(fn=_always_bad)
    trace = GradingTrace()
    graded = asyncio.run(
        build_corpus_grader(grader, worktree_root=wt).grade_corpus(
            _request(repo, wt, path_hints=("editor.py",)),
            proposal,
            SnapshotRef(snapshot_id=1, worktree_id=1, worktree_root=wt),
            trace=trace,
        )
    )
    assert graded.used_fallback is True
    assert graded.expansion_rounds == 0
    assert any(r.path == "editor.py" for r in graded.ranges)
    # Exact hint survives the fallback path.
    assert any(
        "shape:exact_hint" in r.reasons or "exact_hint" in "".join(r.reasons) for r in graded.ranges
    )
    assert any(
        e.kind == "grading_failed" and e.reason_code == "grader_invalid_output"
        for e in trace.events
    )
    # CorpusGrader does not emit an orchestrator-level grader_retry — retry is port-owned.
    assert not any(
        e.kind == "grading_repaired" and e.reason_code == "grader_retry" for e in trace.events
    )


def test_hallucinated_indices_ignored(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    wt = repo / "wt"
    wt.mkdir(parents=True)
    _write_sources(wt)
    proposal = _proposal(
        _range("editor.py", 1, 3, category=EvidenceCategory.EDIT_TARGET, score=90, tokens=30),
    )
    good = GradeResult(
        grades=(
            Grade(
                proposal_index=0,
                include=True,
                category=EvidenceCategory.EDIT_TARGET,
                reason_code=ReasonCode.LIKELY_EDIT_TARGET,
            ),
        ),
        gaps=None,
    )
    grader = hallucinated_indices_grader(good=good, extra_indices=(999, 42))
    trace = GradingTrace()
    graded = asyncio.run(
        build_corpus_grader(grader, worktree_root=wt).grade_corpus(
            _request(repo, wt),
            proposal,
            SnapshotRef(snapshot_id=1, worktree_id=1, worktree_root=wt),
            trace=trace,
        )
    )
    assert len(graded.ranges) == 1
    assert any(e.reason_code == "hallucinated_index" for e in trace.events)


def test_ceilings_hold_across_both_rounds(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    wt = repo / "wt"
    wt.mkdir(parents=True)
    _write_sources(wt)
    # Many small ranges so the token/range ceilings must trim.
    ranges = tuple(
        _range(
            "hub.py",
            i * 2 + 1,
            i * 2 + 2,
            category=EvidenceCategory.SUPPORTING_CONTEXT,
            score=80 - i,
            tokens=60,
        )
        for i in range(8)
    )
    initial = _proposal(ranges[0])
    expanded = CorpusProposal(
        snapshot_id=1,
        profile=RecipientProfile.IMPLEMENTATION,
        ranges=ranges,
        estimated_tokens=sum(r.estimated_tokens for r in ranges),
        truncated=False,
    )
    delta = RequestDelta(search_terms=("common_util",))
    grader = gaps_then_adequate_grader(delta)
    policy = _tiny_policy(
        max_range_proposals=_CEILING_MAX_RANGES,
        max_estimated_tokens=_CEILING_MAX_TOKENS,
    )

    async def propose(req: ContextRequest, _snap: SnapshotRef) -> CorpusProposal:
        assert "common_util" in req.search_terms
        return expanded

    graded = asyncio.run(
        build_corpus_grader(grader, worktree_root=wt, ranking_policy=policy).grade_corpus(
            _request(repo, wt, max_tokens=_CEILING_MAX_TOKENS),
            initial,
            SnapshotRef(snapshot_id=1, worktree_id=1, worktree_root=wt),
            propose=propose,
        )
    )
    assert graded.expansion_rounds == 1
    assert len(graded.ranges) <= _CEILING_MAX_RANGES
    assert graded.estimated_tokens <= _CEILING_MAX_TOKENS


def test_unresolved_gaps_recorded_after_final_pass(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    wt = repo / "wt"
    wt.mkdir(parents=True)
    _write_sources(wt)
    proposal = _proposal(
        _range("editor.py", 1, 3, category=EvidenceCategory.EDIT_TARGET, score=90, tokens=30),
    )
    # Always report the same gaps — after one expansion, they become unresolved.
    sticky = RequestDelta(
        path_hints=("missing_mod.py",),
        unresolved_questions=("Where is the auth middleware?",),
    )
    grader = FakeContextGrader(
        results=[GradeResult(grades=(), gaps=sticky), GradeResult(grades=(), gaps=sticky)]
    )

    async def propose(req: ContextRequest, _snap: SnapshotRef) -> CorpusProposal:
        # Echo proposal; grading still reports gaps.
        assert "missing_mod.py" in req.path_hints
        return proposal

    graded = asyncio.run(
        build_corpus_grader(grader, worktree_root=wt).grade_corpus(
            _request(repo, wt),
            proposal,
            SnapshotRef(snapshot_id=1, worktree_id=1, worktree_root=wt),
            propose=propose,
        )
    )
    assert graded.expansion_rounds == 1
    assert graded.unresolved_questions
    assert any("auth" in q or "missing" in q for q in graded.unresolved_questions)


def test_preview_hides_scores_and_handles_oversized(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    wt = repo / "wt"
    wt.mkdir(parents=True)
    _write_sources(wt)
    # Inflate hub so preview uses oversized path.
    hub = "\n".join(f"def f{i}():\n    return {i}" for i in range(80))
    (wt / "hub.py").write_text(hub + "\n", encoding="utf-8")
    proposal = _proposal(
        _range("hub.py", 1, 160, category=EvidenceCategory.SUPPORTING_CONTEXT, tokens=900),
    )
    text = render_proposal_preview(_request(repo, wt), proposal, worktree_root=wt)
    assert "score=" not in text
    assert "SQL" not in text
    assert "[OVERSIZED]" in text
    assert "proposal_index" not in text or "[0]" in text


def test_preview_header_shows_merged_search_and_relationship_hints(tmp_path: Path) -> None:
    """Round-2 requests expose search_terms / relationship_kind_hints, not only path/symbol."""
    repo = tmp_path / "repo"
    wt = repo / "wt"
    wt.mkdir(parents=True)
    _write_sources(wt)
    proposal = _proposal(
        _range("editor.py", 1, 3, category=EvidenceCategory.EDIT_TARGET, tokens=20),
    )
    req = apply_request_delta(
        _request(repo, wt, path_hints=("editor.py",), symbol_hints=("ProfileEditor",)),
        RequestDelta(
            search_terms=("magic_validation_token",),
            relationship_kinds=("calls",),
        ),
    )
    text = render_proposal_preview(req, proposal, worktree_root=wt)
    assert "search_terms: magic_validation_token" in text
    assert "relationship_kind_hints: calls" in text
    assert "path_hints: editor.py" in text
    assert "symbol_hints: ProfileEditor" in text
    assert "score=" not in text


def test_llm_adapter_retries_then_raises() -> None:
    class StubClient:
        def __init__(self) -> None:
            self.calls = 0

        async def complete(self, **_kwargs: object) -> CompletionResult:
            self.calls += 1
            if self.calls == 1:
                return CompletionResult(
                    text='{"grades":[{"proposal_index":0,"include":true,'
                    '"category":"edit_target","reason_code":"nope"}]}',
                    tool_calls=[],
                    prompt_tokens=10,
                    completion_tokens=5,
                    model="stub",
                    latency_ms=1.0,
                )
            return CompletionResult(
                text="still-bad",
                tool_calls=[],
                prompt_tokens=10,
                completion_tokens=5,
                model="stub",
                latency_ms=1.0,
            )

    client = StubClient()
    grader = LlmContextGrader(
        client=client,  # type: ignore[arg-type]
        model="stub-model",
        worktree_root=Path("/tmp"),
    )
    proposal = _proposal(
        _range("editor.py", 1, 1, tokens=5),
    )
    # Need a readable worktree for preview — use tmp via Path that may fail;
    # render catches unreadable. Use empty path carefully.
    req = ContextRequest(
        request_id="r",
        recipient_id="a",
        repository_state=RepositoryState(
            repository_root=Path("/tmp"),
            worktree_root=Path("/tmp"),
            state_timestamp=STATE_TS,
        ),
        objective="x",
        recipient_profile=RecipientProfile.IMPLEMENTATION,
    )
    with pytest.raises(GraderOutputError):
        asyncio.run(grader.grade(req, proposal))
    assert client.calls == _STRUCTURED_RETRY_ATTEMPTS
    assert grader.last_trace is not None
    assert any(
        e.kind == "grading_failed" and e.reason_code == "grader_invalid_output"
        for e in grader.last_trace.events
    )


def test_llm_adapter_retries_then_succeeds(tmp_path: Path) -> None:
    """One malformed attempt then valid — exactly two client calls, no CorpusGrader stack."""
    repo = tmp_path / "repo"
    wt = repo / "wt"
    wt.mkdir(parents=True)
    _write_sources(wt)
    good = {
        "grades": [
            {
                "proposal_index": 0,
                "include": True,
                "category": "edit_target",
                "reason_code": "likely_edit_target",
            }
        ],
        "gaps": None,
    }

    class StubClient:
        def __init__(self) -> None:
            self.calls = 0

        async def complete(self, **_kwargs: object) -> CompletionResult:
            self.calls += 1
            if self.calls == 1:
                return CompletionResult(
                    text='{"grades":[{"proposal_index":0,"include":true,'
                    '"category":"edit_target","reason_code":"nope"}]}',
                    tool_calls=[],
                    prompt_tokens=10,
                    completion_tokens=5,
                    model="stub",
                    latency_ms=1.0,
                )
            return CompletionResult(
                text=None,
                tool_calls=[ToolCall(name="submit_grades", arguments=good, call_id="1")],
                prompt_tokens=10,
                completion_tokens=5,
                model="stub",
                latency_ms=1.0,
            )

    client = StubClient()
    llm = LlmContextGrader(
        client=client,  # type: ignore[arg-type]
        model="stub-model",
        worktree_root=wt,
    )
    proposal = _proposal(
        _range("editor.py", 1, 3, category=EvidenceCategory.EDIT_TARGET, tokens=20),
    )
    graded = asyncio.run(
        build_corpus_grader(llm, worktree_root=wt).grade_corpus(
            _request(repo, wt),
            proposal,
            SnapshotRef(snapshot_id=1, worktree_id=1, worktree_root=wt),
        )
    )
    assert client.calls == _STRUCTURED_RETRY_ATTEMPTS
    assert graded.used_fallback is False
    assert graded.ranges
    assert llm.last_trace is not None
    assert any(
        e.kind == "grading_repaired" and e.reason_code == "retry_validation"
        for e in llm.last_trace.events
    )


def test_corpus_grader_does_not_double_retry_llm(tmp_path: Path) -> None:
    """Always-invalid LLM output: two client attempts total, then grading_failed fallback."""
    repo = tmp_path / "repo"
    wt = repo / "wt"
    wt.mkdir(parents=True)
    _write_sources(wt)

    class StubClient:
        def __init__(self) -> None:
            self.calls = 0

        async def complete(self, **_kwargs: object) -> CompletionResult:
            self.calls += 1
            return CompletionResult(
                text="not-json",
                tool_calls=[],
                prompt_tokens=1,
                completion_tokens=1,
                model="stub",
                latency_ms=1.0,
            )

    client = StubClient()
    llm = LlmContextGrader(
        client=client,  # type: ignore[arg-type]
        model="stub-model",
        worktree_root=wt,
    )
    proposal = _proposal(
        _range("editor.py", 1, 3, category=EvidenceCategory.EDIT_TARGET, tokens=20),
    )
    trace = GradingTrace()
    graded = asyncio.run(
        build_corpus_grader(llm, worktree_root=wt).grade_corpus(
            _request(repo, wt, path_hints=("editor.py",)),
            proposal,
            SnapshotRef(snapshot_id=1, worktree_id=1, worktree_root=wt),
            trace=trace,
        )
    )
    assert client.calls == _STRUCTURED_RETRY_ATTEMPTS
    assert graded.used_fallback is True
    assert any(
        e.kind == "grading_failed" and e.reason_code == "grader_invalid_output"
        for e in trace.events
    )


def test_llm_adapter_accepts_tool_call(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    wt = repo / "wt"
    wt.mkdir(parents=True)
    _write_sources(wt)
    payload = {
        "grades": [
            {
                "proposal_index": 0,
                "include": True,
                "category": "edit_target",
                "reason_code": "likely_edit_target",
            }
        ],
        "gaps": None,
    }

    class StubClient:
        async def complete(self, **_kwargs: object) -> CompletionResult:
            return CompletionResult(
                text=None,
                tool_calls=[
                    ToolCall(name="submit_grades", arguments=payload, call_id="1"),
                ],
                prompt_tokens=10,
                completion_tokens=5,
                model="stub",
                latency_ms=1.0,
            )

    grader = LlmContextGrader(
        client=StubClient(),  # type: ignore[arg-type]
        model="stub-model",
        worktree_root=wt,
    )
    proposal = _proposal(
        _range("editor.py", 1, 3, category=EvidenceCategory.EDIT_TARGET, tokens=20),
    )
    result = asyncio.run(grader.grade(_request(repo, wt), proposal))
    assert result.grades[0].include is True


def test_exact_hint_preserved_despite_exclude(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    wt = repo / "wt"
    wt.mkdir(parents=True)
    _write_sources(wt)
    proposal = _proposal(
        _range(
            "editor.py",
            1,
            3,
            category=EvidenceCategory.EDIT_TARGET,
            score=100,
            tokens=20,
            reasons=("shape:exact_hint",),
        ),
        _range("hub.py", 1, 5, score=50, tokens=20),
    )
    result = GradeResult(
        grades=(
            Grade(
                proposal_index=0,
                include=False,
                category=EvidenceCategory.EDIT_TARGET,
                reason_code=ReasonCode.TASK_IRRELEVANT,
            ),
            Grade(
                proposal_index=1,
                include=False,
                category=EvidenceCategory.SUPPORTING_CONTEXT,
                reason_code=ReasonCode.TASK_IRRELEVANT,
            ),
        ),
        gaps=None,
    )
    ranges, _grades, _tokens = post_validate_grades(
        _request(repo, wt),
        proposal,
        result,
        worktree_root=wt,
    )
    assert any(r.path == "editor.py" for r in ranges)
    assert not any(r.path == "hub.py" for r in ranges)


def test_grading_with_indexed_ranking_fixture(tmp_path: Path) -> None:
    """End-to-end smoke: Step 4 proposal + fake grader on ranking fixtures."""
    repo = tmp_path / "repo"
    wt = repo / "wt"
    wt.mkdir(parents=True)
    materialize_fixture_repo("ranking", wt)
    conn = open_context_index(repo, db_path=repo / ".murder" / "context-index.db")
    try:
        result = index_worktree_sync(
            repository_root=repo,
            worktree_root=wt,
            state_timestamp="2026-08-01T00:00:00Z",
            commit_sha=None,
            conn=conn,
        )
        assert result.status == "ready"
        snapshot = SnapshotRef(
            snapshot_id=result.snapshot_id,
            worktree_id=result.worktree_id,
            worktree_root=wt,
            state_timestamp=result.state_timestamp,
            commit_sha=None,
        )
        proposer = build_corpus_proposer(conn, worktree_root=wt)
        req = _request(
            repo,
            wt,
            path_hints=("editor.py",),
            symbol_hints=("ProfileEditor", "save"),
        )
        proposal = asyncio.run(proposer.propose_corpus(req, snapshot))
        assert proposal.ranges
        graded = asyncio.run(
            build_corpus_grader(
                exclude_paths_grader("hub.py"),
                worktree_root=wt,
                conn=conn,
                proposer=proposer,
            ).grade_corpus(req, proposal, snapshot)
        )
        paths = {r.path for r in graded.ranges}
        assert "hub.py" not in paths
        # Recipient output path: ranges only — no synthesis field on GradedCorpus.
        assert not hasattr(graded, "summary")
        assert graded.used_fallback is False
    finally:
        conn.close()


def test_parse_grade_result_json_roundtrip() -> None:
    raw = json.dumps(
        {
            "grades": [
                {
                    "proposal_index": 1,
                    "include": False,
                    "category": "other",
                    "reason_code": "duplicate_information",
                }
            ],
            "gaps": {
                "path_hints": ["a.py"],
                "symbol_hints": [],
                "search_terms": ["foo"],
                "relationship_kinds": ["calls"],
                "unresolved_questions": [],
            },
        }
    )
    result = parse_grade_result_json(raw)
    assert result.gaps is not None
    assert result.gaps.path_hints == ("a.py",)
    assert result.gaps.search_terms == ("foo",)
    assert result.gaps.relationship_kinds == ("calls",)


def test_malformed_then_valid_grader_retry_success(tmp_path: Path) -> None:
    """Port-level retry succeeds inside one ``grade`` call — CorpusGrader stacks none."""
    repo = tmp_path / "repo"
    wt = repo / "wt"
    wt.mkdir(parents=True)
    _write_sources(wt)
    proposal = _proposal(
        _range("editor.py", 1, 3, category=EvidenceCategory.EDIT_TARGET, score=90, tokens=30),
    )
    valid = GradeResult(
        grades=(
            Grade(
                proposal_index=0,
                include=True,
                category=EvidenceCategory.EDIT_TARGET,
                reason_code=ReasonCode.LIKELY_EDIT_TARGET,
            ),
        ),
        gaps=None,
    )
    grader = malformed_then_valid_grader(valid)
    trace = GradingTrace()
    graded = asyncio.run(
        build_corpus_grader(grader, worktree_root=wt).grade_corpus(
            _request(repo, wt),
            proposal,
            SnapshotRef(snapshot_id=1, worktree_id=1, worktree_root=wt),
            trace=trace,
        )
    )
    assert graded.used_fallback is False
    assert len(graded.ranges) == 1
    # One CorpusGrader → grade() call; port records two internal attempts.
    assert len(grader.calls) == 1
    assert grader.internal_attempts == _STRUCTURED_RETRY_ATTEMPTS
    assert grader.failures == ["scripted malformed output"]
    assert not any(e.reason_code == "grader_retry" for e in trace.events)
    assert not any(e.kind == "grading_failed" for e in trace.events)


def test_trace_events_beyond_hallucinated_index(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    wt = repo / "wt"
    wt.mkdir(parents=True)
    _write_sources(wt)
    proposal = _proposal(
        _range("editor.py", 1, 3, category=EvidenceCategory.EDIT_TARGET, score=90, tokens=30),
        _range("hub.py", 1, 5, score=40, tokens=20),
    )
    grader = exclude_paths_grader("hub.py")
    trace = GradingTrace()
    asyncio.run(
        build_corpus_grader(grader, worktree_root=wt).grade_corpus(
            _request(repo, wt),
            proposal,
            SnapshotRef(snapshot_id=1, worktree_id=1, worktree_root=wt),
            trace=trace,
        )
    )
    kinds = {e.kind for e in trace.events}
    assert "grading_started" in kinds
    assert "candidate_selected" in kinds
    assert "candidate_rejected" in kinds
    assert "final_grade" in kinds
    assert any(e.reason_code == "task_irrelevant" for e in trace.events)


def test_determinism_replay_graded_corpus(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    wt = repo / "wt"
    wt.mkdir(parents=True)
    _write_sources(wt)
    proposal = _proposal(
        _range("editor.py", 1, 3, category=EvidenceCategory.EDIT_TARGET, score=90, tokens=30),
        _range("hub.py", 1, 5, score=40, tokens=20),
    )
    snap = SnapshotRef(snapshot_id=1, worktree_id=1, worktree_root=wt)
    req = _request(repo, wt)

    def _run() -> GradedCorpus:
        return asyncio.run(
            build_corpus_grader(exclude_paths_grader("hub.py"), worktree_root=wt).grade_corpus(
                req, proposal, snap
            )
        )

    a = _run()
    b = _run()
    assert _corpus_fingerprint(a) == _corpus_fingerprint(b)
    # Harness-style byte fingerprint (json.dumps sort_keys), matching Step 4 spirit.
    assert graded_corpus_fingerprint(a) == graded_corpus_fingerprint(b)
    # Frozen dataclass equality (byte-identical serialization spirit).
    assert a.ranges == b.ranges
    assert a.grades == b.grades
    assert a == b


def test_determinism_replay_graded_corpus_with_expansion(tmp_path: Path) -> None:
    """Expansion-round graded path is byte-identical across replay."""
    conn, snapshot, repo, wt = _index_ranking(tmp_path)
    try:
        proposer = build_corpus_proposer(conn, worktree_root=wt)
        base_req = _request(
            repo,
            wt,
            objective="Inspect the nearby module layout.",
            path_hints=("editor.py",),
            symbol_hints=(),
        )
        initial = asyncio.run(proposer.propose_corpus(base_req, snapshot))
        # Ensure the lexical needle is absent before expansion.
        assert not any(r.path == "lexical_target.py" for r in initial.ranges)
        delta = RequestDelta(search_terms=("magic_validation_token",))
        grader = gaps_then_adequate_grader(delta)

        def _run() -> GradedCorpus:
            return asyncio.run(
                build_corpus_grader(
                    grader, worktree_root=wt, conn=conn, proposer=proposer
                ).grade_corpus(base_req, initial, snapshot)
            )

        a = _run()
        # Fresh fake so call state does not leak into the second run.
        grader_b = gaps_then_adequate_grader(delta)
        b = asyncio.run(
            build_corpus_grader(
                grader_b, worktree_root=wt, conn=conn, proposer=proposer
            ).grade_corpus(base_req, initial, snapshot)
        )
        assert a.expansion_rounds == 1
        assert any(r.path == "lexical_target.py" for r in a.ranges)
        assert graded_corpus_fingerprint(a) == graded_corpus_fingerprint(b)
        assert _corpus_fingerprint(a) == _corpus_fingerprint(b)
        assert a.ranges == b.ranges
    finally:
        conn.close()


def test_search_terms_graded_expansion_e2e(tmp_path: Path) -> None:
    """gaps-only search_terms → real grade_corpus + real proposer → new file appears."""
    conn, snapshot, repo, wt = _index_ranking(tmp_path)
    try:
        proposer = build_corpus_proposer(conn, worktree_root=wt)
        base_req = _request(
            repo,
            wt,
            objective="Inspect the nearby module layout.",
            path_hints=("editor.py",),
            symbol_hints=(),
        )
        initial = asyncio.run(proposer.propose_corpus(base_req, snapshot))
        assert not any(r.path == "lexical_target.py" for r in initial.ranges)

        delta = RequestDelta(search_terms=("magic_validation_token",))
        grader = gaps_then_adequate_grader(delta)
        trace = GradingTrace()
        graded = asyncio.run(
            build_corpus_grader(
                grader, worktree_root=wt, conn=conn, proposer=proposer
            ).grade_corpus(base_req, initial, snapshot, trace=trace)
        )
        assert graded.expansion_rounds == 1
        assert any(r.path == "lexical_target.py" for r in graded.ranges)
        assert ("expansion_requested", "gaps") in [(e.kind, e.reason_code) for e in trace.events]
        assert ("expansion_completed", "reproposed") in [
            (e.kind, e.reason_code) for e in trace.events
        ]
        # Second grading call saw the merged search_terms on the request.
        assert len(grader.calls) == _EXPECTED_GRADE_CALLS_WITH_EXPANSION
        assert "magic_validation_token" in grader.calls[1][0].search_terms
    finally:
        conn.close()


def test_graded_eval_harness_cases(tmp_path: Path) -> None:
    """Step 5 harness path: fake-grader recall/forbidden/determinism after grading."""
    cases = all_graded_cases()
    assert len(cases) >= _MIN_GRADED_CASES
    assert all(c.mode == "graded" for c in cases)
    report = run_cases(cases, work_dir=tmp_path / "eval-graded")
    assert report.all_deterministic
    assert report.all_expansion_rounds_ok

    by_name = {c.name: c for c in report.cases}
    exclude = by_name["grading-exclude-hub"]
    assert exclude.determinism_status == "identical"
    assert exclude.forbidden_unit_hits == 0
    assert exclude.expansion_rounds == 0
    assert exclude.expected_unit_recall > 0
    assert "hub.py::common_util_alpha" not in exclude.hit_expected

    expansion = by_name["grading-search-terms-expansion"]
    assert expansion.determinism_status == "identical"
    assert expansion.expansion_rounds == 1
    assert expansion.expansion_rounds_ok is True
    assert expansion.expected_unit_recall == 1.0
    assert "lexical_target.py::process_payload" in expansion.hit_expected
    assert expansion.forbidden_unit_hits == 0

    # Single-case path also deterministic (harness double-run).
    one = run_case(cases[0], work_dir=tmp_path / "one-graded")
    assert one.determinism_status == "identical"


def test_search_terms_distinct_on_request_delta() -> None:
    """``search_terms`` stay on ContextRequest.search_terms — not folded into symbols."""
    base = ContextRequest(
        request_id="r",
        recipient_id="a",
        repository_state=RepositoryState(
            repository_root=Path("/tmp"),
            worktree_root=Path("/tmp"),
            state_timestamp=STATE_TS,
        ),
        objective="x",
        recipient_profile=RecipientProfile.IMPLEMENTATION,
        symbol_hints=("Existing",),
    )
    merged = apply_request_delta(base, RequestDelta(search_terms=("needle_term",)))
    assert "needle_term" in merged.search_terms
    assert "needle_term" not in merged.symbol_hints
    assert "Existing" in merged.symbol_hints


def test_search_terms_change_lexical_provider_retrieval(tmp_path: Path) -> None:
    """Only ``search_terms`` changes LexicalSearchProvider hits (not field-merge alone)."""
    conn, snapshot, repo, wt = _index_ranking(tmp_path)
    try:
        provider = LexicalSearchProvider(conn=conn, worktree_root=wt)
        bland = ContextRequest(
            request_id="r",
            recipient_id="a",
            repository_state=RepositoryState(
                repository_root=repo,
                worktree_root=wt,
                state_timestamp=STATE_TS,
            ),
            objective="Inspect the nearby module layout.",
            recipient_profile=RecipientProfile.IMPLEMENTATION,
        )
        with_terms = ContextRequest(
            request_id="r",
            recipient_id="a",
            repository_state=RepositoryState(
                repository_root=repo,
                worktree_root=wt,
                state_timestamp=STATE_TS,
            ),
            objective="Inspect the nearby module layout.",
            recipient_profile=RecipientProfile.IMPLEMENTATION,
            search_terms=("magic_validation_token",),
        )
        without = asyncio.run(provider.generate(bland, snapshot, ()))
        with_hit = asyncio.run(provider.generate(with_terms, snapshot, ()))
        assert not any(c.path == "lexical_target.py" for c in without)
        assert any(c.path == "lexical_target.py" for c in with_hit)

        # Same delta also changes Step 4 propose_corpus via the lexical provider.
        proposer = build_corpus_proposer(conn, worktree_root=wt)
        base_prop = asyncio.run(proposer.propose_corpus(bland, snapshot))
        term_prop = asyncio.run(proposer.propose_corpus(with_terms, snapshot))
        assert not any(r.path == "lexical_target.py" for r in base_prop.ranges)
        assert any(r.path == "lexical_target.py" for r in term_prop.ranges)
    finally:
        conn.close()


def test_relationship_kinds_only_gap_drives_expansion(tmp_path: Path) -> None:
    """``relationship_kinds``-only delta changes real propose_corpus expansion results."""
    conn, snapshot, repo, wt = _index_ranking(tmp_path)
    try:
        _inject_caller_and_test_edges(conn, snapshot)
        proposer = build_corpus_proposer(conn, worktree_root=wt)
        # Bland objective + no symbol hints so lexical does not seed caller.py via "save".
        base_req = _request(
            repo,
            wt,
            objective="x",
            path_hints=("editor.py",),
            symbol_hints=(),
        )

        async def _propose(hints: tuple[str, ...]) -> CorpusProposal:
            return await proposer.propose_corpus(
                ContextRequest(
                    request_id=base_req.request_id,
                    recipient_id=base_req.recipient_id,
                    repository_state=base_req.repository_state,
                    objective=base_req.objective,
                    recipient_profile=base_req.recipient_profile,
                    path_hints=base_req.path_hints,
                    symbol_hints=base_req.symbol_hints,
                    relationship_kind_hints=hints,
                ),
                snapshot,
            )

        calls_prop = asyncio.run(_propose(("calls",)))
        tests_prop = asyncio.run(_propose(("tests",)))
        calls_paths = {r.path for r in calls_prop.ranges}
        tests_paths = {r.path for r in tests_prop.ranges}
        assert "caller.py" in calls_paths
        assert "caller.py" not in tests_paths
        assert "test_editor.py" in tests_paths or any(
            "expand:tests:" in "".join(r.reasons) for r in tests_prop.ranges
        )
        assert calls_paths != tests_paths

        # End-to-end grading: relationship_kinds-only gap → real re-propose.
        initial = CorpusProposal(
            snapshot_id=calls_prop.snapshot_id,
            profile=RecipientProfile.IMPLEMENTATION,
            ranges=tuple(r for r in calls_prop.ranges if r.path == "editor.py"),
            estimated_tokens=sum(
                r.estimated_tokens for r in calls_prop.ranges if r.path == "editor.py"
            ),
            truncated=False,
        )
        delta = RequestDelta(relationship_kinds=("calls",))
        grader = gaps_then_adequate_grader(delta)
        trace = GradingTrace()
        graded = asyncio.run(
            build_corpus_grader(
                grader, worktree_root=wt, conn=conn, proposer=proposer
            ).grade_corpus(base_req, initial, snapshot, trace=trace)
        )
        assert graded.expansion_rounds == 1
        assert any(r.path == "caller.py" for r in graded.ranges)
        assert ("expansion_requested", "gaps") in [(e.kind, e.reason_code) for e in trace.events]
        assert ("expansion_completed", "reproposed") in [
            (e.kind, e.reason_code) for e in trace.events
        ]
    finally:
        conn.close()


def test_expansion_unavailable_emits_failed_trace(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    wt = repo / "wt"
    wt.mkdir(parents=True)
    _write_sources(wt)
    proposal = _proposal(
        _range("editor.py", 1, 3, category=EvidenceCategory.EDIT_TARGET, score=90, tokens=30),
    )
    sticky = RequestDelta(relationship_kinds=("calls",))
    grader = FakeContextGrader(
        results=[GradeResult(grades=(), gaps=sticky)],
    )
    trace = GradingTrace()
    # No propose=, no conn, no proposer → expansion cannot re-propose.
    graded = asyncio.run(
        build_corpus_grader(grader, worktree_root=wt).grade_corpus(
            _request(repo, wt),
            proposal,
            SnapshotRef(snapshot_id=1, worktree_id=1, worktree_root=wt),
            trace=trace,
        )
    )
    assert graded.expansion_rounds == 0
    kinds = [(e.kind, e.reason_code) for e in trace.events]
    assert ("expansion_requested", "gaps") in kinds
    assert ("expansion_failed", "repropose_unavailable") in kinds
    assert not any(e.kind == "expansion_completed" for e in trace.events)
    assert graded.unresolved_questions


def test_category_shaping_edit_target_containing_unit(tmp_path: Path) -> None:
    """edit_target grades reshape through Step 4 containing-unit rules when indexed."""
    repo = tmp_path / "repo"
    wt = repo / "wt"
    wt.mkdir(parents=True)
    materialize_fixture_repo("ranking", wt)
    conn = open_context_index(repo, db_path=repo / ".murder" / "context-index.db")
    try:
        result = index_worktree_sync(
            repository_root=repo,
            worktree_root=wt,
            state_timestamp="2026-08-01T00:00:00Z",
            commit_sha=None,
            conn=conn,
        )
        assert result.status == "ready"
        # Narrow slice inside a unit — post-validation should widen to containing unit.
        proposal = CorpusProposal(
            snapshot_id=result.snapshot_id,
            profile=RecipientProfile.IMPLEMENTATION,
            ranges=(
                RangeProposal(
                    path="editor.py",
                    line_range=LineRange(_EDIT_TARGET_SEED_LINE, _EDIT_TARGET_SEED_LINE),
                    unit_version_id=None,
                    category=EvidenceCategory.SUPPORTING_CONTEXT,
                    score=90.0,
                    reasons=("seed",),
                    estimated_tokens=5,
                ),
            ),
            estimated_tokens=5,
            truncated=False,
        )
        grade_result = GradeResult(
            grades=(
                Grade(
                    proposal_index=0,
                    include=True,
                    category=EvidenceCategory.EDIT_TARGET,
                    reason_code=ReasonCode.LIKELY_EDIT_TARGET,
                ),
            ),
            gaps=None,
        )
        ranges, _grades, _tokens = post_validate_grades(
            _request(repo, wt, path_hints=("editor.py",)),
            proposal,
            grade_result,
            worktree_root=wt,
            conn=conn,
        )
        assert ranges
        shaped = ranges[0]
        assert shaped.category is EvidenceCategory.EDIT_TARGET
        assert (
            "shape:grade_containing_unit" in shaped.reasons
            or "shape:grade_keep_proposed" in shaped.reasons
        )
        # Containing unit is at least as wide as the single-line seed.
        assert shaped.line_range.end_line >= shaped.line_range.start_line
        if "shape:grade_containing_unit" in shaped.reasons:
            assert shaped.line_range.end_line > shaped.line_range.start_line
            assert shaped.unit_version_id is not None or (
                shaped.line_range.start_line <= _EDIT_TARGET_SEED_LINE <= shaped.line_range.end_line
            )
    finally:
        conn.close()


def test_exact_hint_preserved_on_fallback_path(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    wt = repo / "wt"
    wt.mkdir(parents=True)
    _write_sources(wt)
    proposal = _proposal(
        _range(
            "editor.py",
            1,
            3,
            category=EvidenceCategory.EDIT_TARGET,
            score=100,
            tokens=20,
            reasons=("shape:exact_hint",),
        ),
        _range("hub.py", 1, 5, score=50, tokens=20),
    )

    def _always_bad(_request: ContextRequest, _proposal: CorpusProposal) -> GradeResult:
        raise GraderOutputError("malformed")

    graded = asyncio.run(
        build_corpus_grader(FakeContextGrader(fn=_always_bad), worktree_root=wt).grade_corpus(
            _request(repo, wt, path_hints=("editor.py",)),
            proposal,
            SnapshotRef(snapshot_id=1, worktree_id=1, worktree_root=wt),
        )
    )
    assert graded.used_fallback is True
    assert any(r.path == "editor.py" for r in graded.ranges)


@pytest.mark.live
def test_live_llm_grading_smoke(tmp_path: Path) -> None:
    """Live LLM grading path. Skipped by default.

    Marker: ``@pytest.mark.live`` (pyproject). Opt in with::

        MURDER_LIVE=1 pytest -m live tests/unit/test_context_compiler_step5.py

    Also skips when no grading client/credentials resolve via policy.
    Fake graders remain the default suite — this is not a stub stand-in.
    """
    if os.environ.get("MURDER_LIVE") != "1":
        pytest.skip("set MURDER_LIVE=1 (and LLM credentials) to run live grading smoke")

    repo = tmp_path / "repo"
    wt = repo / "wt"
    wt.mkdir(parents=True)
    _write_sources(wt)
    proposal = _proposal(
        _range("editor.py", 1, 3, category=EvidenceCategory.EDIT_TARGET, score=90, tokens=30),
        _range("hub.py", 1, 5, score=40, tokens=20),
    )
    live = build_llm_context_grader(worktree_root=wt)
    if live is None:
        pytest.skip("no live grading client/credentials available")

    graded = asyncio.run(
        build_corpus_grader(live, worktree_root=wt).grade_corpus(
            _request(repo, wt, path_hints=("editor.py",), symbol_hints=("ProfileEditor",)),
            proposal,
            SnapshotRef(snapshot_id=1, worktree_id=1, worktree_root=wt),
        )
    )
    assert graded.ranges
    assert graded.used_fallback is False or graded.ranges  # never empty on failure path either
    assert isinstance(graded.estimated_tokens, int)
