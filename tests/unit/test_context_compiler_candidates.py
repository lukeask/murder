"""Focused tests for Context Assembler 2 candidate providers."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from murder.context_compiler.candidates import (
    SCORE_EXACT_PATH,
    SCORE_EXACT_QUALIFIED_SYMBOL,
    SCORE_EXACT_UNIQUE_SYMBOL,
    Candidate,
    CompositeCandidateProvider,
    ExactHintsProvider,
    SnapshotRef,
    StructuralNeighborProvider,
    candidate_identity,
)
from murder.context_compiler.candidates.active_diff import ActiveDiffProvider
from murder.context_compiler.candidates.models import (
    SCORE_ACTIVE_DIFF_OVERLAP,
    SCORE_AMBIGUOUS_PATH,
    SCORE_AMBIGUOUS_SYMBOL,
    SCORE_DIRECT_STRUCTURAL,
    merge_candidates,
)
from murder.context_compiler.extraction.models import REL_CALLS, REL_IMPORTS
from murder.context_compiler.models import (
    ContextRequest,
    RecipientProfile,
    RepositoryState,
)
from murder.context_compiler.persistence import (
    FileExtractionReplacement,
    RelationshipInput,
    SemanticUnitVersionInput,
    create_building_snapshot,
    get_or_create_worktree,
    mark_snapshot_ready,
    open_context_index,
    replace_file_extraction,
)
from murder.context_compiler.persistence.files import get_file
from murder.context_compiler.persistence.semantic_units import (
    list_semantic_unit_versions_for_file_version,
)

STATE_TS = datetime(2026, 8, 1, 8, 0, tzinfo=timezone.utc)


def _request(
    repo: Path,
    worktree: Path,
    *,
    path_hints: tuple[str, ...] = (),
    symbol_hints: tuple[str, ...] = (),
    objective: str = "find helpers",
) -> ContextRequest:
    return ContextRequest(
        request_id="req-1",
        recipient_id="agent-1",
        repository_state=RepositoryState(
            repository_root=repo,
            worktree_root=worktree,
            state_timestamp=STATE_TS,
            commit_sha=None,
        ),
        objective=objective,
        recipient_profile=RecipientProfile.IMPLEMENTATION,
        path_hints=path_hints,
        symbol_hints=symbol_hints,
        trace_id="trace-1",
    )


def _seed_index(tmp_path: Path):
    """Build a small ready snapshot with two modules and a relationship."""
    repo = tmp_path / "repo"
    wt = repo / "wt"
    wt.mkdir(parents=True)
    (wt / "pkg").mkdir()
    (wt / "other").mkdir()
    (wt / "pkg" / "alpha.py").write_text(
        "def helper():\n    return 1\n\ndef unused():\n    return 2\n",
        encoding="utf-8",
    )
    (wt / "other" / "alpha.py").write_text(
        "def helper():\n    return 9\n",
        encoding="utf-8",
    )
    (wt / "pkg" / "beta.py").write_text(
        "from pkg.alpha import helper\n\ndef caller():\n    return helper()\n",
        encoding="utf-8",
    )

    conn = open_context_index(repo, db_path=repo / ".murder" / "context-index.db")
    worktree = get_or_create_worktree(conn, repository_root=repo, worktree_root=wt)
    snap = create_building_snapshot(
        conn,
        worktree_id=worktree.worktree_id,
        state_timestamp="2026-08-01T10:00:00",
    )

    alpha_pkg = replace_file_extraction(
        conn,
        snapshot_id=snap.snapshot_id,
        worktree_id=worktree.worktree_id,
        extraction=FileExtractionReplacement(
            relative_path="pkg/alpha.py",
            source_hash="hash-alpha-pkg",
            byte_count=40,
            line_count=5,
            parse_status="parsed",
            extractor_version="test-1",
            language="python",
            units=(
                SemanticUnitVersionInput(
                    logical_key="function:pkg.alpha.helper",
                    language_kind="function",
                    qualified_name="pkg.alpha.helper",
                    unqualified_name="helper",
                    start_line=1,
                    end_line=2,
                    exported=True,
                ),
                SemanticUnitVersionInput(
                    logical_key="function:pkg.alpha.unused",
                    language_kind="function",
                    qualified_name="pkg.alpha.unused",
                    unqualified_name="unused",
                    start_line=4,
                    end_line=5,
                    exported=True,
                ),
            ),
        ),
    )
    alpha_other = replace_file_extraction(
        conn,
        snapshot_id=snap.snapshot_id,
        worktree_id=worktree.worktree_id,
        extraction=FileExtractionReplacement(
            relative_path="other/alpha.py",
            source_hash="hash-alpha-other",
            byte_count=20,
            line_count=2,
            parse_status="parsed",
            extractor_version="test-1",
            language="python",
            units=(
                SemanticUnitVersionInput(
                    logical_key="function:other.alpha.helper",
                    language_kind="function",
                    qualified_name="other.alpha.helper",
                    unqualified_name="helper",
                    start_line=1,
                    end_line=2,
                    exported=True,
                ),
            ),
        ),
    )

    alpha_units = list_semantic_unit_versions_for_file_version(conn, alpha_pkg.file_version_id)
    helper_unit = next(u for u in alpha_units if u.unqualified_name == "helper")
    alpha_file = get_file(conn, alpha_pkg.file_id)
    assert alpha_file is not None

    replace_file_extraction(
        conn,
        snapshot_id=snap.snapshot_id,
        worktree_id=worktree.worktree_id,
        extraction=FileExtractionReplacement(
            relative_path="pkg/beta.py",
            source_hash="hash-beta",
            byte_count=50,
            line_count=4,
            parse_status="parsed",
            extractor_version="test-1",
            language="python",
            units=(
                SemanticUnitVersionInput(
                    logical_key="function:pkg.beta.caller",
                    language_kind="function",
                    qualified_name="pkg.beta.caller",
                    unqualified_name="caller",
                    start_line=3,
                    end_line=4,
                    exported=True,
                ),
            ),
            relationships=(
                RelationshipInput(
                    relation_kind=REL_IMPORTS,
                    confidence=0.9,
                    resolution_method="test",
                    target_file_id=alpha_file.file_id,
                    start_line=1,
                    end_line=1,
                ),
                RelationshipInput(
                    relation_kind=REL_CALLS,
                    confidence=0.85,
                    resolution_method="test",
                    source_unit_logical_key="function:pkg.beta.caller",
                    target_unit_id=helper_unit.unit_id,
                    start_line=4,
                    end_line=4,
                ),
            ),
        ),
    )

    mark_snapshot_ready(conn, snap.snapshot_id)
    snapshot = SnapshotRef(
        snapshot_id=snap.snapshot_id,
        worktree_id=worktree.worktree_id,
        worktree_root=wt,
        state_timestamp=snap.state_timestamp,
    )
    return conn, repo, wt, snapshot, helper_unit, alpha_other


@pytest.mark.asyncio
async def test_exact_path_hint_deterministic(tmp_path: Path) -> None:
    conn, repo, wt, snapshot, _helper, _other = _seed_index(tmp_path)
    try:
        provider = ExactHintsProvider(conn)
        request = _request(repo, wt, path_hints=("pkg/alpha.py",))
        first = await provider.generate(request, snapshot, ())
        second = await provider.generate(request, snapshot, ())
        assert first == second
        paths = [c.path for c in first if c.unit_id is None]
        assert paths == ["pkg/alpha.py"]
        assert first[0].raw_score == SCORE_EXACT_PATH
        assert "exact_path_hint" in first[0].reasons
        # Top-level units included.
        unit_names = {c.metadata.get("qualified_name") for c in first if c.unit_id is not None}
        assert "pkg.alpha.helper" in unit_names
        assert "pkg.alpha.unused" in unit_names
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_exact_path_ambiguity_preserved(tmp_path: Path) -> None:
    conn, repo, wt, snapshot, _helper, _other = _seed_index(tmp_path)
    try:
        provider = ExactHintsProvider(conn)
        request = _request(repo, wt, path_hints=("alpha.py",))
        hits = await provider.generate(request, snapshot, ())
        file_hits = [c for c in hits if c.unit_id is None]
        assert len(file_hits) == 2
        assert {c.path for c in file_hits} == {"pkg/alpha.py", "other/alpha.py"}
        assert all(c.raw_score == SCORE_AMBIGUOUS_PATH for c in file_hits)
        assert all("ambiguous_path_hint" in c.reasons for c in file_hits)
        # Deterministic order across runs.
        again = await provider.generate(request, snapshot, ())
        assert [c.path for c in again if c.unit_id is None] == [c.path for c in file_hits]
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_exact_symbol_unique_and_ambiguous(tmp_path: Path) -> None:
    conn, repo, wt, snapshot, _helper, _other = _seed_index(tmp_path)
    try:
        provider = ExactHintsProvider(conn)

        unique = await provider.generate(_request(repo, wt, symbol_hints=("unused",)), snapshot, ())
        assert len(unique) == 1
        assert unique[0].raw_score == SCORE_EXACT_UNIQUE_SYMBOL
        assert "exact_unique_symbol_hint" in unique[0].reasons

        qualified = await provider.generate(
            _request(repo, wt, symbol_hints=("pkg.alpha.helper",)), snapshot, ()
        )
        assert len(qualified) == 1
        assert qualified[0].raw_score == SCORE_EXACT_QUALIFIED_SYMBOL

        ambiguous = await provider.generate(
            _request(repo, wt, symbol_hints=("helper",)), snapshot, ()
        )
        assert len(ambiguous) == 2
        assert all(c.raw_score == SCORE_AMBIGUOUS_SYMBOL for c in ambiguous)
        assert all("ambiguous_symbol_hint" in c.reasons for c in ambiguous)
        assert {c.metadata.get("qualified_name") for c in ambiguous} == {
            "pkg.alpha.helper",
            "other.alpha.helper",
        }
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_structural_exactly_one_hop(tmp_path: Path) -> None:
    conn, repo, wt, snapshot, helper_unit, _other = _seed_index(tmp_path)
    try:
        exact = ExactHintsProvider(conn)
        structural = StructuralNeighborProvider(conn, seed_provider=exact)

        # Seed on beta → one hop reaches alpha (imports) and helper (calls).
        request = _request(repo, wt, path_hints=("pkg/beta.py",))
        neighbors = await structural.generate(request, snapshot, ())
        assert neighbors
        assert all(c.raw_score == SCORE_DIRECT_STRUCTURAL for c in neighbors)
        neighbor_paths = {c.path for c in neighbors}
        assert "pkg/alpha.py" in neighbor_paths

        # Seeds that are already the callee: incoming should find beta caller
        # but must not recurse further (no second-hop neighbors of beta).
        request2 = _request(repo, wt, symbol_hints=("pkg.alpha.helper",))
        from_helper = await structural.generate(request2, snapshot, ())
        assert any(c.path == "pkg/beta.py" for c in from_helper)
        # One hop only: neighbors stay within direct edges of the seed.
        assert all(c.path in {"pkg/alpha.py", "pkg/beta.py"} for c in from_helper)
        from_helper_names = {
            c.metadata.get("qualified_name") for c in from_helper if c.unit_id is not None
        }
        # Multi-hop would surface unused via beta→alpha→unused; must not.
        assert "pkg.alpha.unused" not in from_helper_names
        assert any(c.metadata.get("qualified_name") == "pkg.beta.caller" for c in from_helper)
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_composite_dedupe_ordering_bounds(tmp_path: Path) -> None:
    conn, repo, wt, snapshot, _helper, _other = _seed_index(tmp_path)
    try:

        class _ProvA:
            PROVIDER_ID = "prov_a"

            async def generate(self, request, snapshot, prior_evidence):
                del request, snapshot, prior_evidence
                return (
                    Candidate(
                        path="pkg/alpha.py",
                        unit_id=None,
                        unit_version_id=None,
                        start_line=None,
                        end_line=None,
                        candidate_kind="file",
                        reasons=("from_a",),
                        provider="prov_a",
                        raw_score=50.0,
                        metadata={},
                    ),
                    Candidate(
                        path="pkg/beta.py",
                        unit_id=None,
                        unit_version_id=None,
                        start_line=None,
                        end_line=None,
                        candidate_kind="file",
                        reasons=("from_a_beta",),
                        provider="prov_a",
                        raw_score=40.0,
                        metadata={},
                    ),
                    Candidate(
                        path="other/alpha.py",
                        unit_id=None,
                        unit_version_id=None,
                        start_line=None,
                        end_line=None,
                        candidate_kind="file",
                        reasons=("from_a_other",),
                        provider="prov_a",
                        raw_score=30.0,
                        metadata={},
                    ),
                )

        class _ProvB:
            PROVIDER_ID = "prov_b"

            async def generate(self, request, snapshot, prior_evidence):
                del request, snapshot, prior_evidence
                return (
                    Candidate(
                        path="pkg/alpha.py",
                        unit_id=None,
                        unit_version_id=None,
                        start_line=None,
                        end_line=None,
                        candidate_kind="file",
                        reasons=("from_b",),
                        provider="prov_b",
                        raw_score=90.0,
                        metadata={},
                    ),
                )

        composite = CompositeCandidateProvider(
            providers=(_ProvA(), _ProvB()),
            max_per_provider=2,
            max_total=2,
        )
        request = _request(repo, wt)
        result = await composite.generate(request, snapshot, ())

        # Dedupe pkg/alpha.py; strongest score wins; reasons merged.
        alpha = next(c for c in result if c.path == "pkg/alpha.py")
        assert alpha.raw_score == 90.0
        assert set(alpha.reasons) == {"from_a", "from_b"}

        # max_per_provider=2 caps A before merge; max_total=2 caps output.
        assert len(result) == 2
        # Deterministic score-desc ordering.
        assert result[0].raw_score >= result[1].raw_score
        assert [c.path for c in result] == sorted(
            [c.path for c in result],
            key=lambda p: (
                -next(x.raw_score or 0 for x in result if x.path == p),
                p,
            ),
        )

        # Identity merge helper sanity.
        merged = merge_candidates(result[0], result[0])
        assert candidate_identity(merged) == candidate_identity(result[0])
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_unique_suffix_path_hint(tmp_path: Path) -> None:
    conn, repo, wt, snapshot, _helper, _other = _seed_index(tmp_path)
    try:
        provider = ExactHintsProvider(conn)
        hits = await provider.generate(_request(repo, wt, path_hints=("beta.py",)), snapshot, ())
        file_hits = [c for c in hits if c.unit_id is None]
        assert len(file_hits) == 1
        assert file_hits[0].path == "pkg/beta.py"
        assert "unique_path_suffix_hint" in file_hits[0].reasons
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_active_diff_maps_hunks_to_owning_units(tmp_path: Path) -> None:
    """Changed lines resolve to overlapping / owning semantic units."""
    conn, repo, wt, snapshot, helper_unit, _other = _seed_index(tmp_path)
    try:

        class _StubDiff(ActiveDiffProvider):
            async def _changed_paths(self, root: Path) -> list[str]:
                del root
                return ["pkg/alpha.py"]

            async def _hunk_ranges(
                self, root: Path, paths: list[str]
            ) -> dict[str, list[tuple[int, int]]]:
                del root, paths
                # Line 1 is inside ``helper`` (def helper on line 1).
                return {"pkg/alpha.py": [(1, 1)]}

        provider = _StubDiff(conn=conn, worktree_root=wt)
        hits = await provider.generate(_request(repo, wt), snapshot, ())
        assert hits
        assert all(c.raw_score == SCORE_ACTIVE_DIFF_OVERLAP for c in hits)
        assert any(c.candidate_kind == "diff_path" and c.path == "pkg/alpha.py" for c in hits)
        unit_hits = [c for c in hits if c.unit_id is not None]
        assert unit_hits
        assert any(c.unit_id == helper_unit.unit_id for c in unit_hits)
        assert any(
            "active_diff_owning_unit" in c.reasons or "active_diff_unit_overlap" in c.reasons
            for c in unit_hits
        )
        # Candidates are structured records only — no recipient-visible prose fields.
        for c in hits:
            assert not hasattr(c, "prose")
            assert not hasattr(c, "summary")
            assert not hasattr(c, "prompt")
    finally:
        conn.close()


def test_candidate_record_has_no_recipient_prose_fields() -> None:
    """Providers emit structured Candidate rows, never rendered brief text."""
    fields = set(Candidate.__dataclass_fields__)
    assert "prose" not in fields
    assert "summary" not in fields
    assert "prompt" not in fields
    assert "rendered" not in fields
    assert {"path", "reasons", "provider", "raw_score", "metadata"} <= fields
