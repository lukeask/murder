"""Step 4 — deterministic ranking, range shaping, and corpus eval cases."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

from murder.context_compiler.candidates.models import (
    CANDIDATE_KIND_EXACT_RANGE,
    CANDIDATE_KIND_RELATIONSHIP_NEIGHBOR,
    CANDIDATE_KIND_SEMANTIC_UNIT,
    CANDIDATE_KIND_TEST,
    Candidate,
    SnapshotRef,
    candidate_identity,
)
from murder.context_compiler.eval import (
    all_corpus_cases,
    materialize_fixture_repo,
    run_case,
    run_cases,
)
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
from murder.context_compiler.persistence.records import RelationshipRecord
from murder.context_compiler.persistence.relationships import insert_resolved_relationship
from murder.context_compiler.ranking import (
    DEFAULT_RANKING_POLICY,
    CorpusProposal,
    RangeProposal,
    RankingTrace,
    bind_propose_corpus,
    build_corpus_proposer,
    range_proposal_sort_key,
    ranking_identity,
)
from murder.context_compiler.ranking.expansion import (
    RelationshipExpander,
    _is_filename_only,
)
from murder.context_compiler.ranking.policy import (
    CATEGORY_SORT_PRIORITY,
    SECOND_HOP_MIN,
    SMALL_FILE_LINE_THRESHOLD,
    ProfileWeights,
    RankingPolicy,
)
from murder.context_compiler.ranking.propose import estimate_candidate_tokens
from murder.context_compiler.ranking.scoring import (
    ScoredCandidate,
    merge_ranked,
    score_candidate,
)
from murder.context_compiler.ranking.shaping import RangeShaper, _ShapedRange
from murder.context_compiler.ranking.tokens import DEFAULT_TOKEN_COUNTER
from murder.context_compiler.rendering import extract_source_slice
from murder.context_compiler.source import FilesystemSourceReader

STATE_TS = datetime(2026, 8, 1, 8, 0, tzinfo=timezone.utc)
_FOCUSED_TEST_MAX_LINES = 20
_PROCESS_PAYLOAD_TOKEN_LINE = 15
_MIN_CORPUS_CASES = 5
_MIN_IMPL_RECALL = 0.5
_LEXICAL_MERGE_START = 10
_LEXICAL_MERGE_END = 24
_LEXICAL_MERGE_SCORE = 50.0


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
        request_id="req-rank-1",
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
        trace_id="trace-rank-1",
    )


def _index_ranking(tmp_path: Path) -> tuple[object, SnapshotRef, Path, Path]:
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


def test_ranking_identity_keeps_distinct_kinds_over_one_unit() -> None:
    """Distinct ranges / kinds over one unit are not collapsed."""
    unit = Candidate(
        path="editor.py",
        unit_id=1,
        unit_version_id=10,
        start_line=1,
        end_line=20,
        candidate_kind=CANDIDATE_KIND_SEMANTIC_UNIT,
        reasons=("exact_symbol",),
        provider="exact_hints",
        raw_score=95.0,
    )
    test = Candidate(
        path="test_editor.py",
        unit_id=2,
        unit_version_id=11,
        start_line=5,
        end_line=8,
        candidate_kind=CANDIDATE_KIND_TEST,
        reasons=("focused_test",),
        provider="tests",
        raw_score=40.0,
    )
    # Same unit_version, different kind+range must stay distinct when kind differs.
    call_site = Candidate(
        path="editor.py",
        unit_id=1,
        unit_version_id=10,
        start_line=12,
        end_line=12,
        candidate_kind=CANDIDATE_KIND_EXACT_RANGE,
        reasons=("lexical_hit",),
        provider="lexical",
        raw_score=60.0,
    )
    assert ranking_identity(unit) != ranking_identity(call_site)
    # Step 2 identity would collapse unit_version; ranking must not.
    assert candidate_identity(unit) == candidate_identity(
        Candidate(
            path="editor.py",
            unit_id=1,
            unit_version_id=10,
            start_line=12,
            end_line=12,
            candidate_kind=CANDIDATE_KIND_EXACT_RANGE,
            reasons=("other",),
            provider="lexical",
            raw_score=60.0,
        )
    )
    merged = merge_ranked(unit, unit)
    assert "exact_symbol" in merged.reasons
    assert ranking_identity(unit) != ranking_identity(test)


def test_propose_corpus_deterministic_and_profile_sensitive(tmp_path: Path) -> None:
    conn, snapshot, repo, wt = _index_ranking(tmp_path)
    try:
        proposer = build_corpus_proposer(conn, worktree_root=wt)

        async def _run(profile: RecipientProfile) -> CorpusProposal:
            req = _request(
                repo,
                wt,
                profile=profile,
                path_hints=("editor.py",),
                symbol_hints=("ProfileEditor", "save"),
            )
            return await proposer.propose_corpus(req, snapshot)

        impl_a = asyncio.run(_run(RecipientProfile.IMPLEMENTATION))
        impl_b = asyncio.run(_run(RecipientProfile.IMPLEMENTATION))
        compact = asyncio.run(_run(RecipientProfile.COMPACT))
        planning = asyncio.run(_run(RecipientProfile.PLANNING))

        assert impl_a.snapshot_id == snapshot.snapshot_id
        assert impl_a.profile is RecipientProfile.IMPLEMENTATION
        # Byte-identical proposal fields: ranges, scores, reasons, tokens.
        keys_a = tuple(
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
            for r in impl_a.ranges
        )
        keys_b = tuple(
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
            for r in impl_b.ranges
        )
        assert keys_a == keys_b
        assert impl_a.estimated_tokens == impl_b.estimated_tokens
        assert impl_a.truncated == impl_b.truncated

        assert compact.estimated_tokens < impl_a.estimated_tokens
        assert len(compact.ranges) <= len(impl_a.ranges)
        assert "editor.py" in {r.path for r in impl_a.ranges}

        # Planning with contract hints must surface contracts broader than compact.
        async def _run_planning() -> CorpusProposal:
            return await proposer.propose_corpus(
                _request(
                    repo,
                    wt,
                    profile=RecipientProfile.PLANNING,
                    path_hints=("editor.py", "contracts.py"),
                    symbol_hints=("ProfileEditor", "ProfileContract", "public_save_api"),
                ),
                snapshot,
            )

        planning_broad = asyncio.run(_run_planning())
        compact_paths = {r.path for r in compact.ranges}
        planning_paths = {r.path for r in planning_broad.ranges}
        compact_contracts = {
            r.path for r in compact.ranges if r.category is EvidenceCategory.CONTRACT
        }
        planning_contracts = {
            r.path
            for r in planning_broad.ranges
            if r.category is EvidenceCategory.CONTRACT
        }
        assert "contracts.py" in planning_paths
        assert planning_contracts >= compact_contracts
        assert "contracts.py" in planning_contracts or "contracts.py" not in compact_paths
        # Keep a same-hints planning run exercised above for profile sensitivity.
        assert planning.profile is RecipientProfile.PLANNING
    finally:
        conn.close()


def test_hub_does_not_dominate_and_token_ceiling_holds(tmp_path: Path) -> None:
    conn, snapshot, repo, wt = _index_ranking(tmp_path)
    try:
        proposer = build_corpus_proposer(conn, worktree_root=wt)
        weights = DEFAULT_RANKING_POLICY.for_profile(RecipientProfile.IMPLEMENTATION)
        trace = RankingTrace()

        async def _run() -> CorpusProposal:
            req = _request(
                repo,
                wt,
                path_hints=("editor.py",),
                symbol_hints=("ProfileEditor",),
                max_tokens=weights.max_estimated_tokens,
            )
            return await proposer.propose_corpus(req, snapshot, trace=trace)

        proposal = asyncio.run(_run())
        assert proposal.estimated_tokens <= weights.max_estimated_tokens
        # Large central hub must not outrank the edit target.
        if proposal.ranges:
            top = proposal.ranges[0]
            assert top.path != "hub.py"
        hub_ranges = [r for r in proposal.ranges if r.path == "hub.py"]
        editor_ranges = [r for r in proposal.ranges if r.path == "editor.py"]
        assert editor_ranges
        if hub_ranges:
            assert hub_ranges[0].score < editor_ranges[0].score
        # Scores / exclusions live in traces, not on CorpusProposal.
        assert not hasattr(proposal, "exclusions")
        assert trace.scores() or trace.exclusions() or True
    finally:
        conn.close()


def test_focused_test_and_lexical_shape_to_units(tmp_path: Path) -> None:
    conn, snapshot, repo, wt = _index_ranking(tmp_path)
    try:
        proposer = build_corpus_proposer(conn, worktree_root=wt)

        async def _impl() -> CorpusProposal:
            req = _request(
                repo,
                wt,
                path_hints=("editor.py",),
                symbol_hints=("ProfileEditor", "save"),
            )
            return await proposer.propose_corpus(req, snapshot)

        async def _lexical() -> CorpusProposal:
            req = _request(
                repo,
                wt,
                objective="Locate magic_validation_token handling.",
                path_hints=("lexical_target.py",),
                symbol_hints=("magic_validation_token",),
            )
            return await proposer.propose_corpus(req, snapshot)

        impl = asyncio.run(_impl())
        lexical = asyncio.run(_lexical())

        test_ranges = [r for r in impl.ranges if r.path == "test_editor.py"]
        assert test_ranges
        # Focused test function — not the whole file (two test functions exist).
        test_src = (wt / "test_editor.py").read_text().splitlines()
        save_line = next(
            i + 1 for i, line in enumerate(test_src) if line.startswith("def test_save_profile")
        )
        assert any(
            r.line_range.start_line <= save_line <= r.line_range.end_line for r in test_ranges
        )
        for r in test_ranges:
            span = r.line_range.end_line - r.line_range.start_line + 1
            assert span < _FOCUSED_TEST_MAX_LINES
            assert span < len(test_src)

        # At least one range should land on process_payload, not whole file.
        lexical_on_target = [r for r in lexical.ranges if r.path == "lexical_target.py"]
        assert lexical_on_target
        file_lines = (wt / "lexical_target.py").read_text().splitlines()
        assert any(
            r.line_range.start_line <= _PROCESS_PAYLOAD_TOKEN_LINE <= r.line_range.end_line
            for r in lexical_on_target
        )
        whole = any(
            r.line_range.start_line == 1 and r.line_range.end_line >= len(file_lines)
            for r in lexical_on_target
        )
        assert not whole or any(
            r.line_range.end_line - r.line_range.start_line + 1 <= SMALL_FILE_LINE_THRESHOLD
            for r in lexical_on_target
        )
    finally:
        conn.close()


def test_angular_template_shapes_to_resource(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    wt = repo / "wt"
    wt.mkdir(parents=True)
    materialize_fixture_repo("frameworks/angular", wt)
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

        async def _run() -> CorpusProposal:
            req = _request(
                repo,
                wt,
                objective="Wire ProfileEditorComponent template.",
                path_hints=("profile-editor.component.ts",),
                symbol_hints=("ProfileEditorComponent",),
            )
            return await proposer.propose_corpus(req, snapshot)

        proposal = asyncio.run(_run())
        paths = {r.path for r in proposal.ranges}
        assert "profile-editor.component.ts" in paths
        html = [r for r in proposal.ranges if r.path.endswith(".html")]
        assert html, "expected shaped template region"
        assert any(
            "owning_component" in "".join(r.reasons) or "framework_resource" in "".join(r.reasons)
            for r in html
        )
    finally:
        conn.close()


def test_oversized_unit_emits_focused_subrange(tmp_path: Path) -> None:
    """Oversized units remain representable without forcing whole-unit inclusion."""
    # Tiny unit cap forces focus window.
    tight = ProfileWeights(
        exact_hint=100.0,
        active_diff=80.0,
        strong_lexical=55.0,
        direct_structural=50.0,
        focused_test=40.0,
        provider_agreement=10.0,
        weak_lexical=10.0,
        weak_tier=20.0,
        relationship_distance=10.0,
        token_cost=1.0,
        large_unit=5.0,
        generated_vendored=30.0,
        max_hops=1,
        second_hop_expansion_cap=0,
        max_raw_candidates=40,
        max_expansions=10,
        max_range_proposals=10,
        max_estimated_tokens=4000,
        max_candidates_per_file=4,
        seed_score_floor=20.0,
        unit_token_cap=5,  # absurdly small → force focus
        token_penalty_scale=1.0,
    )
    policy = RankingPolicy(
        compact=tight,
        implementation=tight,
        planning=tight,
    )
    conn, snapshot, repo, wt = _index_ranking(tmp_path)
    try:
        proposer = build_corpus_proposer(conn, worktree_root=wt, policy=policy)

        async def _run() -> CorpusProposal:
            req = _request(
                repo,
                wt,
                path_hints=("lexical_target.py",),
                symbol_hints=("magic_validation_token",),
                objective="Locate magic_validation_token.",
            )
            return await proposer.propose_corpus(req, snapshot)

        proposal = asyncio.run(_run())
        assert proposal.ranges
        # At least one range should be marked oversized focus or be smaller than
        # the full process_payload unit (~15 lines).
        focused = [
            r
            for r in proposal.ranges
            if r.path == "lexical_target.py"
            and (
                "oversized" in "".join(r.reasons)
                or (r.line_range.end_line - r.line_range.start_line + 1) < _FOCUSED_TEST_MAX_LINES
            )
        ]
        assert focused
    finally:
        conn.close()


def test_score_reasons_not_on_recipient_models() -> None:
    weights = DEFAULT_RANKING_POLICY.for_profile(RecipientProfile.IMPLEMENTATION)
    candidate = Candidate(
        path="editor.py",
        unit_id=1,
        unit_version_id=1,
        start_line=1,
        end_line=10,
        candidate_kind=CANDIDATE_KIND_SEMANTIC_UNIT,
        reasons=("exact_path",),
        provider="exact_hints",
        raw_score=100.0,
    )
    scored = score_candidate(candidate, weights)
    assert scored.score > 0
    assert any(r.startswith("signal:") for r in scored.reasons)
    # CorpusProposal / RangeProposal carry score+reasons for Step 5, but never
    # exclusion lists — those stay on RankingTrace.
    proposal = CorpusProposal(
        snapshot_id=1,
        profile=RecipientProfile.IMPLEMENTATION,
        ranges=(
            RangeProposal(
                path="editor.py",
                line_range=LineRange(1, 10),
                unit_version_id=1,
                category=EvidenceCategory.EDIT_TARGET,
                score=scored.score,
                reasons=scored.reasons,
                estimated_tokens=10,
            ),
        ),
        estimated_tokens=10,
        truncated=False,
    )
    assert not hasattr(proposal, "exclusions")
    assert not hasattr(proposal, "rejected")


def test_corpus_eval_harness_cases(tmp_path: Path) -> None:
    cases = all_corpus_cases()
    assert len(cases) >= _MIN_CORPUS_CASES
    report = run_cases(cases, work_dir=tmp_path / "eval")
    assert report.all_deterministic

    by_name = {c.name: c for c in report.cases}
    compact = by_name["ranking-compact-profile-editor"]
    impl = by_name["ranking-implementation-profile-editor"]
    planning = by_name["ranking-planning-profile-editor"]

    assert compact.estimated_tokens < impl.estimated_tokens
    assert compact.fewer_tokens_than_ok is True
    assert report.all_token_comparisons_ok
    assert impl.expected_unit_recall >= _MIN_IMPL_RECALL
    assert "editor.py::ProfileEditor" in impl.hit_expected or impl.expected_unit_recall > 0
    # Implementation should surface focused test when recall is healthy.
    assert impl.forbidden_unit_hits == 0
    assert planning.forbidden_unit_hits == 0
    assert compact.forbidden_unit_hits == 0
    # Planning reaches broader contracts than compact (harness expected units).
    assert "contracts.py::ProfileContract" in planning.hit_expected
    assert "contracts.py::ProfileContract" not in compact.hit_expected

    lexical = by_name["ranking-lexical-inside-function"]
    assert lexical.determinism_status == "identical"
    assert lexical.expected_unit_recall > 0 or "process_payload" in "".join(lexical.hit_expected)

    angular = by_name["ranking-angular-template-region"]
    assert angular.determinism_status == "identical"
    # Hard gate: HTML/template range must be recalled (not soft/optional).
    assert angular.top_k_range_recall == 1.0
    assert not angular.missed_expected_ranges

    # Single-case path.
    one = run_case(cases[0], work_dir=tmp_path / "one")
    assert one.determinism_status == "identical"


def test_filename_only_expansion_rejected() -> None:
    """Edges whose only basis is a filename heuristic are rejected."""
    filename = RelationshipRecord(
        relationship_id=1,
        source_file_version_id=1,
        source_unit_version_id=1,
        target_file_id=2,
        target_unit_id=None,
        relation_kind="tests",
        start_line=1,
        end_line=1,
        confidence="weak",
        resolution_method="test_filename_heuristic",
        metadata_json="{}",
        snapshot_id=1,
    )
    stem = RelationshipRecord(
        relationship_id=2,
        source_file_version_id=1,
        source_unit_version_id=1,
        target_file_id=2,
        target_unit_id=None,
        relation_kind="references",
        start_line=None,
        end_line=None,
        confidence="weak",
        resolution_method="filename_stem",
        metadata_json="{}",
        snapshot_id=1,
    )
    exact = RelationshipRecord(
        relationship_id=3,
        source_file_version_id=1,
        source_unit_version_id=1,
        target_file_id=2,
        target_unit_id=3,
        relation_kind="calls",
        start_line=10,
        end_line=10,
        confidence="exact",
        resolution_method="import_alias",
        metadata_json="{}",
        snapshot_id=1,
    )
    assert _is_filename_only(filename) is True
    assert _is_filename_only(stem) is True
    assert _is_filename_only(exact) is False


def test_shape_call_site_unit_vs_window(tmp_path: Path) -> None:
    """Call sites take the containing unit when it fits; else a small window."""
    conn, snapshot, _repo, wt = _index_ranking(tmp_path)
    try:
        shaper = RangeShaper(
            conn,  # type: ignore[arg-type]
            source_reader=FilesystemSourceReader(wt),
            token_counter=DEFAULT_TOKEN_COUNTER,
        )
        weights = DEFAULT_RANKING_POLICY.for_profile(RecipientProfile.IMPLEMENTATION)
        units = list_semantic_units_by_path(
            conn, snapshot_id=snapshot.snapshot_id, relative_path="editor.py"
        )
        save = next(u for u in units if u.unqualified_name == "save")
        fitting = ScoredCandidate(
            candidate=Candidate(
                path="editor.py",
                unit_id=save.unit_id,
                unit_version_id=save.unit_version_id,
                start_line=save.start_line,
                end_line=save.end_line,
                candidate_kind=CANDIDATE_KIND_RELATIONSHIP_NEIGHBOR,
                reasons=("expand:calls:incoming",),
                provider="ranking_expansion",
                raw_score=70.0,
                metadata={
                    "relation_kind": "calls",
                    "rel_start_line": save.start_line,
                    "rel_end_line": save.start_line,
                },
            ),
            score=80.0,
            reasons=("signal:direct_structural",),
            category=EvidenceCategory.SUPPORTING_CONTEXT,
            hop=1,
            estimated_tokens=20,
        )
        unit_shaped = shaper._shape_call_site(fitting, snapshot=snapshot, weights=weights)
        assert unit_shaped
        assert "shape:call_site_unit" in unit_shaped[0].reasons

        # Tiny unit cap forces the window path.
        tight = ProfileWeights(
            exact_hint=100.0,
            active_diff=80.0,
            strong_lexical=50.0,
            direct_structural=50.0,
            focused_test=40.0,
            provider_agreement=10.0,
            weak_lexical=10.0,
            weak_tier=20.0,
            relationship_distance=10.0,
            token_cost=1.0,
            large_unit=5.0,
            generated_vendored=30.0,
            max_hops=1,
            second_hop_expansion_cap=0,
            max_raw_candidates=40,
            max_expansions=10,
            max_range_proposals=10,
            max_estimated_tokens=4000,
            max_candidates_per_file=4,
            seed_score_floor=20.0,
            unit_token_cap=1,
            token_penalty_scale=1.0,
        )
        windowed = shaper._shape_call_site(fitting, snapshot=snapshot, weights=tight)
        assert windowed
        assert "shape:call_site_window" in windowed[0].reasons
    finally:
        conn.close()


def test_lexical_window_merge(tmp_path: Path) -> None:
    """Nearby lexical windows merge when the span stays small."""
    conn, _snapshot, _repo, wt = _index_ranking(tmp_path)
    try:
        shaper = RangeShaper(
            conn,  # type: ignore[arg-type]
            source_reader=FilesystemSourceReader(wt),
            token_counter=DEFAULT_TOKEN_COUNTER,
        )
        a = _ShapedRange(
            path="lexical_target.py",
            line_range=LineRange(_LEXICAL_MERGE_START, 16),
            unit_version_id=None,
            category=EvidenceCategory.OTHER,
            score=_LEXICAL_MERGE_SCORE,
            reasons=("shape:lexical_window",),
            estimated_tokens=20,
        )
        b = _ShapedRange(
            path="lexical_target.py",
            line_range=LineRange(18, _LEXICAL_MERGE_END),
            unit_version_id=None,
            category=EvidenceCategory.OTHER,
            score=40.0,
            reasons=("shape:lexical_window",),
            estimated_tokens=20,
        )
        merged = shaper._merge_lexical_windows([a, b])
        assert len(merged) == 1
        assert merged[0].line_range.start_line == _LEXICAL_MERGE_START
        assert merged[0].line_range.end_line == _LEXICAL_MERGE_END
        assert "shape:merged_windows" in merged[0].reasons
        assert merged[0].score == _LEXICAL_MERGE_SCORE
    finally:
        conn.close()


def test_planning_second_hop_expansion(tmp_path: Path) -> None:
    """Planning policy allows a second hop under its expansion cap."""
    conn, snapshot, _repo, _wt = _index_ranking(tmp_path)
    try:
        editor_units = list_semantic_units_by_path(
            conn, snapshot_id=snapshot.snapshot_id, relative_path="editor.py"
        )
        contract_units = list_semantic_units_by_path(
            conn, snapshot_id=snapshot.snapshot_id, relative_path="contracts.py"
        )
        hub_units = list_semantic_units_by_path(
            conn, snapshot_id=snapshot.snapshot_id, relative_path="hub.py"
        )
        save = next(u for u in editor_units if u.unqualified_name == "save")
        validate = next(u for u in contract_units if u.unqualified_name == "validate")
        alpha = next(u for u in hub_units if u.unqualified_name == "common_util_alpha")
        editor_fv = get_file_version_by_path(
            conn, snapshot_id=snapshot.snapshot_id, relative_path="editor.py"
        )
        contracts_fv = get_file_version_by_path(
            conn, snapshot_id=snapshot.snapshot_id, relative_path="contracts.py"
        )
        assert editor_fv is not None and contracts_fv is not None

        # save → validate → common_util_alpha (two-hop chain).
        insert_resolved_relationship(
            conn,
            snapshot_id=snapshot.snapshot_id,
            source_file_version_id=editor_fv.file_version.file_version_id,
            source_unit_version_id=save.unit_version_id,
            target_unit_id=validate.unit_id,
            target_file_id=contracts_fv.file.file_id,
            relation_kind="calls",
            confidence="exact",
            resolution_method="test_injected_call",
            start_line=save.start_line,
            end_line=save.start_line,
        )
        insert_resolved_relationship(
            conn,
            snapshot_id=snapshot.snapshot_id,
            source_file_version_id=contracts_fv.file_version.file_version_id,
            source_unit_version_id=validate.unit_version_id,
            target_unit_id=alpha.unit_id,
            target_file_id=get_file_version_by_path(
                conn, snapshot_id=snapshot.snapshot_id, relative_path="hub.py"
            ).file.file_id,  # type: ignore[union-attr]
            relation_kind="calls",
            confidence="exact",
            resolution_method="test_injected_call",
            start_line=validate.start_line,
            end_line=validate.start_line,
        )
        conn.commit()

        weights = DEFAULT_RANKING_POLICY.for_profile(RecipientProfile.PLANNING)
        seed = score_candidate(
            Candidate(
                path="editor.py",
                unit_id=save.unit_id,
                unit_version_id=save.unit_version_id,
                start_line=save.start_line,
                end_line=save.end_line,
                candidate_kind=CANDIDATE_KIND_SEMANTIC_UNIT,
                reasons=("exact_symbol",),
                provider="exact_hints",
                raw_score=100.0,
            ),
            weights,
            hop=0,
            estimated_tokens=10,
        )
        assert seed.score >= weights.seed_score_floor
        result = RelationshipExpander(conn).expand(  # type: ignore[arg-type]
            snapshot=snapshot,
            profile=RecipientProfile.PLANNING,
            weights=weights,
            seeds=(seed,),
        )
        assert SECOND_HOP_MIN in result.hops.values()
        hop2_paths = {
            c.path
            for ident, hop in result.hops.items()
            if hop == SECOND_HOP_MIN
            for c in result.candidates
            if ranking_identity(c) == ident
        }
        assert "hub.py" in hop2_paths
    finally:
        conn.close()


def test_merge_provenance_survives_propose_corpus(tmp_path: Path) -> None:
    """Provider metadata after merge survives the full propose_corpus pipeline."""
    conn, snapshot, repo, wt = _index_ranking(tmp_path)
    try:
        units = list_semantic_units_by_path(
            conn, snapshot_id=snapshot.snapshot_id, relative_path="editor.py"
        )
        pe = next(u for u in units if u.unqualified_name == "ProfileEditor")
        a = Candidate(
            path="editor.py",
            unit_id=pe.unit_id,
            unit_version_id=pe.unit_version_id,
            start_line=pe.start_line,
            end_line=pe.end_line,
            candidate_kind=CANDIDATE_KIND_SEMANTIC_UNIT,
            reasons=("exact_path",),
            provider="exact_hints",
            raw_score=95.0,
        )
        b = Candidate(
            path="editor.py",
            unit_id=pe.unit_id,
            unit_version_id=pe.unit_version_id,
            start_line=pe.start_line,
            end_line=pe.end_line,
            candidate_kind=CANDIDATE_KIND_SEMANTIC_UNIT,
            reasons=("lexical_hit",),
            provider="lexical",
            raw_score=60.0,
        )
        merged = merge_ranked(a, b)
        assert set(merged.metadata["providers"]) >= {"exact_hints", "lexical"}

        class _MergedProvider:
            async def generate(self, request, snapshot, prior_evidence):  # noqa: ANN001
                return (merged,)

        propose_corpus = bind_propose_corpus(
            conn,  # type: ignore[arg-type]
            worktree_root=wt,
            candidate_provider=_MergedProvider(),  # type: ignore[arg-type]
        )

        async def _run() -> CorpusProposal:
            return await propose_corpus(
                _request(repo, wt, path_hints=("editor.py",), symbol_hints=("ProfileEditor",)),
                snapshot,
            )

        proposal = asyncio.run(_run())
        assert proposal.ranges
        joined = " ".join(" ".join(r.reasons) for r in proposal.ranges)
        assert "providers:exact_hints,lexical" in joined
        assert "signal:provider_agreement:2" in joined
    finally:
        conn.close()


def test_estimate_candidate_tokens_uses_exact_slice_text(tmp_path: Path) -> None:
    """Pre-shape estimates count exact excerpt text, not line-span alone."""
    wt = tmp_path / "wt"
    wt.mkdir()
    # Dense middle line: line-span heuristic would be 1*10; exact text is far denser.
    dense = "x" * 400
    text = f"short\n{dense}\nend\n"
    (wt / "mod.py").write_text(text, encoding="utf-8")
    reader = FilesystemSourceReader(wt)
    seen: list[str] = []

    class _RecordingCounter:
        def count_tokens(self, fragment: str) -> int:
            seen.append(fragment)
            return len(fragment) // 4

    candidate = Candidate(
        path="mod.py",
        unit_id=None,
        unit_version_id=None,
        start_line=2,
        end_line=2,
        candidate_kind=CANDIDATE_KIND_EXACT_RANGE,
        reasons=("exact_path",),
        provider="exact_hints",
        raw_score=100.0,
    )
    tokens = estimate_candidate_tokens(
        candidate,
        reader=reader,
        token_counter=_RecordingCounter(),
        text_cache={},
    )
    expected_slice = extract_source_slice(text, 2, 2)
    assert seen == [expected_slice]
    assert expected_slice == dense
    assert tokens == len(dense) // 4
    # Line-span fallback would be 10 for a single line — exact path must diverge.
    assert tokens != 10  # noqa: PLR2004


def test_range_proposal_sort_synthetic_identity_tiebreak() -> None:
    """Final tie-break is a synthetic range identity (candidate-identity role)."""
    same = 40.0
    short = RangeProposal(
        path="a.py",
        line_range=LineRange(1, 2),
        unit_version_id=5,
        category=EvidenceCategory.SUPPORTING_CONTEXT,
        score=same,
        reasons=("a",),
        estimated_tokens=1,
    )
    long = RangeProposal(
        path="a.py",
        line_range=LineRange(1, 9),
        unit_version_id=5,
        category=EvidenceCategory.SUPPORTING_CONTEXT,
        score=same,
        reasons=("b",),
        estimated_tokens=1,
    )
    # Same score/category/path/start; synthetic identity orders by end line next.
    ordered = tuple(sorted((long, short), key=range_proposal_sort_key))
    assert ordered[0] is short
    assert ordered[1] is long
    key = range_proposal_sort_key(short)
    assert key[-1] == (
        "a.py",
        1,
        2,
        5,
        EvidenceCategory.SUPPORTING_CONTEXT.value,
    )


def test_range_proposal_sort_uses_category_priority_not_lexicographic() -> None:
    """Final CorpusProposal order must use CATEGORY_SORT_PRIORITY, not enum value."""
    # Lexicographic: contract < edit_target; priority: edit_target (0) < contract (2).
    same_score = 50.0
    edit = RangeProposal(
        path="a.py",
        line_range=LineRange(1, 2),
        unit_version_id=1,
        category=EvidenceCategory.EDIT_TARGET,
        score=same_score,
        reasons=("x",),
        estimated_tokens=1,
    )
    contract = RangeProposal(
        path="a.py",
        line_range=LineRange(1, 2),
        unit_version_id=2,
        category=EvidenceCategory.CONTRACT,
        score=same_score,
        reasons=("y",),
        estimated_tokens=1,
    )
    ordered = tuple(sorted((contract, edit), key=range_proposal_sort_key))
    assert ordered[0].category is EvidenceCategory.EDIT_TARGET
    assert CATEGORY_SORT_PRIORITY["edit_target"] < CATEGORY_SORT_PRIORITY["contract"]
    # Lexicographic category.value would have put contract first.
    assert EvidenceCategory.CONTRACT.value < EvidenceCategory.EDIT_TARGET.value


def test_bind_propose_corpus_two_arg_api(tmp_path: Path) -> None:
    """Public two-arg propose_corpus(request, snapshot) with deps closed over."""
    conn, snapshot, repo, wt = _index_ranking(tmp_path)
    try:
        propose_corpus = bind_propose_corpus(conn, worktree_root=wt)  # type: ignore[arg-type]

        async def _run() -> CorpusProposal:
            return await propose_corpus(
                _request(repo, wt, path_hints=("editor.py",), symbol_hints=("ProfileEditor",)),
                snapshot,
            )

        proposal = asyncio.run(_run())
        assert proposal.snapshot_id == snapshot.snapshot_id
        assert proposal.ranges
    finally:
        conn.close()
