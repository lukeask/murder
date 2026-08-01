"""Step 3 — snapshot isolation, confidence tiers, fixtures, eval harness."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from murder.context_compiler.eval import (
    CandidateSnapshot,
    EvalCase,
    EvalCaseReport,
    UnitRef,
    all_candidate_cases,
    materialize_fixture_repo,
    run_case,
    run_cases,
)
from murder.context_compiler.eval.fixtures import (
    FIXTURES_ROOT,
    all_corpus_cases,
    all_graded_cases,
)
from murder.context_compiler.eval.runner import _score_case
from murder.context_compiler.extraction import (
    REL_RENDERS_COMPONENT,
    REL_TESTS,
    default_registry,
    reset_default_registry,
)
from murder.context_compiler.indexing import (
    get_file_version_by_path,
    index_worktree_sync,
    list_outgoing_relationships,
    list_references_for_path,
    list_targets_for_reference,
    resolve_snapshot,
)
from murder.context_compiler.indexing.queries import list_semantic_units_by_path
from murder.context_compiler.indexing.resolution_policy import (
    CONFIDENCE_EXACT,
    CONFIDENCE_INFERRED,
    CONFIDENCE_WEAK,
    PRECEDENCE_EXACT_PATH,
    PRECEDENCE_FILENAME_HEURISTIC,
    PRECEDENCE_FRAMEWORK_SELECTOR,
    PRECEDENCE_IMPORTED_ALIAS,
    PRECEDENCE_UNIQUE_UNQUALIFIED,
    tier_for_precedence,
    tier_rank,
)
from murder.context_compiler.models import RecipientProfile
from murder.context_compiler.persistence import (
    SCHEMA_VERSION,
    list_resolved_reference_targets,
    list_resolved_relationships_for_snapshot,
    open_context_index,
)
from murder.context_compiler.persistence.schema import SCHEMA_VERSION as _SV
from murder.context_compiler.persistence.semantic_units import (
    list_semantic_unit_versions_for_file_version,
)


def setup_function() -> None:
    reset_default_registry()


def _index(repo: Path, wt: Path, conn, *, ts: str) -> object:
    return index_worktree_sync(
        repo,
        wt,
        state_timestamp=ts,
        commit_sha=None,
        conn=conn,
    )


def test_schema_version_bumped_for_step3() -> None:
    assert _SV == SCHEMA_VERSION
    # Step 3 introduced resolved_* tables + tier confidence (v2+).
    assert SCHEMA_VERSION > 1


def test_tier_for_precedence_maps_ranks_1_through_5() -> None:
    """Precedence ranks 1–5 map to exact / inferred / weak exactly once each."""
    assert tier_for_precedence(PRECEDENCE_EXACT_PATH) == CONFIDENCE_EXACT
    assert tier_for_precedence(PRECEDENCE_IMPORTED_ALIAS) == CONFIDENCE_EXACT
    assert tier_for_precedence(PRECEDENCE_UNIQUE_UNQUALIFIED) == CONFIDENCE_INFERRED
    assert tier_for_precedence(PRECEDENCE_FRAMEWORK_SELECTOR) == CONFIDENCE_INFERRED
    assert tier_for_precedence(PRECEDENCE_FILENAME_HEURISTIC) == CONFIDENCE_WEAK


def test_snapshot_drift_reused_file_version_does_not_leak_targets(  # noqa: PLR0915
    tmp_path: Path,
) -> None:
    """Release-blocking regression: reused source file_version must not leak
    snapshot A's resolved target into snapshot B.
    """
    repo = tmp_path / "repo"
    wt = repo / "wt"
    wt.mkdir(parents=True)
    materialize_fixture_repo("snapshot_drift", wt)

    conn = open_context_index(repo, db_path=repo / "context-index.db")
    try:
        r_a = _index(repo, wt, conn, ts="2026-08-01T10:00:00Z")
        assert r_a.status == "ready"
        source_a = get_file_version_by_path(
            conn, snapshot_id=r_a.snapshot_id, relative_path="source.py"
        )
        assert source_a is not None
        target_a = get_file_version_by_path(
            conn, snapshot_id=r_a.snapshot_id, relative_path="target.py"
        )
        assert target_a is not None
        units_a = list_semantic_unit_versions_for_file_version(
            conn, target_a.file_version.file_version_id
        )
        greeter_a = next(u for u in units_a if u.unqualified_name == "Greeter")
        assert not any(u.unqualified_name == "extra" for u in units_a)

        refs_a = list_references_for_path(
            conn, snapshot_id=r_a.snapshot_id, relative_path="source.py"
        )
        greeter_refs_a = [r for r in refs_a if "Greeter" in r.identifier]
        assert greeter_refs_a
        targets_a = list_targets_for_reference(
            conn,
            snapshot_id=r_a.snapshot_id,
            reference_id=greeter_refs_a[0].reference_id,
        )
        assert targets_a
        unit_ids_a = {t.target_unit_id for t in targets_a}

        # Change only the exporting file.
        shutil.copy2(
            FIXTURES_ROOT / "snapshot_drift" / "target_b.py",
            wt / "target.py",
        )
        r_b = _index(repo, wt, conn, ts="2026-08-01T11:00:00Z")
        assert r_b.status == "ready"

        source_b = get_file_version_by_path(
            conn, snapshot_id=r_b.snapshot_id, relative_path="source.py"
        )
        assert source_b is not None
        # Source file_version reused.
        assert source_b.file_version.file_version_id == source_a.file_version.file_version_id
        target_b = get_file_version_by_path(
            conn, snapshot_id=r_b.snapshot_id, relative_path="target.py"
        )
        assert target_b is not None
        assert target_b.file_version.file_version_id != target_a.file_version.file_version_id

        # B resolves against B's export state — new body + B-only ``extra()``.
        units_b = list_semantic_unit_versions_for_file_version(
            conn, target_b.file_version.file_version_id
        )
        greeter_b = next(u for u in units_b if u.unqualified_name == "Greeter")
        assert greeter_b.unit_version_id != greeter_a.unit_version_id
        assert any(u.unqualified_name == "extra" for u in units_b)
        # Greeter span grew to cover the new method in B.
        assert greeter_b.end_line > greeter_a.end_line

        # A still exposes its original targets / export shape.
        targets_a_again = list_targets_for_reference(
            conn,
            snapshot_id=r_a.snapshot_id,
            reference_id=greeter_refs_a[0].reference_id,
        )
        assert {t.target_unit_id for t in targets_a_again} == unit_ids_a
        units_a_again = list_semantic_unit_versions_for_file_version(
            conn, target_a.file_version.file_version_id
        )
        assert not any(u.unqualified_name == "extra" for u in units_a_again)

        refs_b = list_references_for_path(
            conn, snapshot_id=r_b.snapshot_id, relative_path="source.py"
        )
        greeter_refs_b = [r for r in refs_b if "Greeter" in r.identifier]
        assert greeter_refs_b
        assert greeter_refs_b[0].reference_id == greeter_refs_a[0].reference_id
        targets_b = list_targets_for_reference(
            conn,
            snapshot_id=r_b.snapshot_id,
            reference_id=greeter_refs_b[0].reference_id,
        )
        assert targets_b
        # Preferred target is still the Greeter logical unit, but B-scoped rows.
        resolved_a = list_resolved_reference_targets(
            conn,
            snapshot_id=r_a.snapshot_id,
            reference_id=greeter_refs_a[0].reference_id,
        )
        resolved_b = list_resolved_reference_targets(
            conn,
            snapshot_id=r_b.snapshot_id,
            reference_id=greeter_refs_b[0].reference_id,
        )
        assert all(t.snapshot_id == r_a.snapshot_id for t in resolved_a)
        assert all(t.snapshot_id == r_b.snapshot_id for t in resolved_b)

        rels_b = list_resolved_relationships_for_snapshot(
            conn,
            snapshot_id=r_b.snapshot_id,
            source_file_version_id=source_b.file_version.file_version_id,
        )
        assert all(r.snapshot_id == r_b.snapshot_id for r in rels_b)
        rels_a = list_resolved_relationships_for_snapshot(
            conn,
            snapshot_id=r_a.snapshot_id,
            source_file_version_id=source_a.file_version.file_version_id,
        )
        assert {r.relationship_id for r in rels_a}.isdisjoint({r.relationship_id for r in rels_b})
    finally:
        conn.close()


def test_resolution_idempotent_replace_no_extraction_mutation(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    wt = repo / "wt"
    wt.mkdir(parents=True)
    materialize_fixture_repo("cross_file", wt)
    conn = open_context_index(repo, db_path=repo / "context-index.db")
    try:
        result = _index(repo, wt, conn, ts="2026-08-01T10:00:00Z")
        assert result.status == "ready"
        snap = result.snapshot_id

        refs_before = list_references_for_path(conn, snapshot_id=snap, relative_path="consumer.py")
        imports_before = conn.execute("SELECT COUNT(*) AS n FROM imports").fetchone()["n"]
        local_rels_before = conn.execute("SELECT COUNT(*) AS n FROM relationships").fetchone()["n"]
        resolved_before = list_resolved_relationships_for_snapshot(conn, snapshot_id=snap)
        assert resolved_before

        summary1 = resolve_snapshot(conn, snap)
        summary2 = resolve_snapshot(conn, snap)
        assert summary2.relationships_added == summary1.relationships_added
        assert summary2.reference_targets_written == summary1.reference_targets_written

        refs_after = list_references_for_path(conn, snapshot_id=snap, relative_path="consumer.py")
        assert [r.reference_id for r in refs_after] == [r.reference_id for r in refs_before]
        assert conn.execute("SELECT COUNT(*) AS n FROM imports").fetchone()["n"] == imports_before
        assert (
            conn.execute("SELECT COUNT(*) AS n FROM relationships").fetchone()["n"]
            == local_rels_before
        )

        resolved_after = list_resolved_relationships_for_snapshot(conn, snapshot_id=snap)
        assert len(resolved_after) == len(resolved_before)
    finally:
        conn.close()


def test_ambiguous_keeps_all_unresolved_stays_queryable(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    wt = repo / "wt"
    wt.mkdir(parents=True)
    materialize_fixture_repo("cross_file", wt)
    conn = open_context_index(repo, db_path=repo / "context-index.db")
    try:
        result = _index(repo, wt, conn, ts="2026-08-01T10:00:00Z")
        assert result.status == "ready"
        refs = list_references_for_path(
            conn, snapshot_id=result.snapshot_id, relative_path="consumer.py"
        )
        shared = [r for r in refs if r.identifier == "shared_name"]
        assert shared
        targets = list_targets_for_reference(
            conn,
            snapshot_id=result.snapshot_id,
            reference_id=shared[0].reference_id,
        )
        # Real ambiguity: util.shared_name and other.shared_name, none preferred.
        assert len(targets) >= 2  # noqa: PLR2004
        assert sum(1 for t in targets if t.is_preferred) == 0
        target_unit_ids = {t.target_unit_id for t in targets}
        util_units = list_semantic_units_by_path(
            conn, snapshot_id=result.snapshot_id, relative_path="util.py"
        )
        other_units = list_semantic_units_by_path(
            conn, snapshot_id=result.snapshot_id, relative_path="other.py"
        )
        util_shared = next(u.unit_id for u in util_units if u.unqualified_name == "shared_name")
        other_shared = next(u.unit_id for u in other_units if u.unqualified_name == "shared_name")
        assert util_shared in target_unit_ids
        assert other_shared in target_unit_ids
    finally:
        conn.close()

    # Unresolved fixture: reference remains queryable with zero targets.
    repo2 = tmp_path / "repo2"
    wt2 = repo2 / "wt"
    wt2.mkdir(parents=True)
    materialize_fixture_repo("unresolved", wt2)
    conn2 = open_context_index(repo2, db_path=repo2 / "context-index.db")
    try:
        result2 = _index(repo2, wt2, conn2, ts="2026-08-01T10:00:00Z")
        assert result2.status == "ready"
        refs2 = list_references_for_path(
            conn2, snapshot_id=result2.snapshot_id, relative_path="caller.py"
        )
        missing = [r for r in refs2 if "TotallyMissing" in r.identifier]
        assert missing
        targets2 = list_targets_for_reference(
            conn2,
            snapshot_id=result2.snapshot_id,
            reference_id=missing[0].reference_id,
        )
        assert targets2 == []
    finally:
        conn2.close()


def test_cross_file_exact_import_and_imported_alias(tmp_path: Path) -> None:
    """Direct resolver assertions — not only the harness path."""
    repo = tmp_path / "repo"
    wt = repo / "wt"
    wt.mkdir(parents=True)
    materialize_fixture_repo("cross_file", wt)
    conn = open_context_index(repo, db_path=repo / "context-index.db")
    try:
        result = _index(repo, wt, conn, ts="2026-08-01T10:00:00Z")
        assert result.status == "ready"
        snap = result.snapshot_id

        consumer = get_file_version_by_path(conn, snapshot_id=snap, relative_path="consumer.py")
        assert consumer is not None
        rels = list_outgoing_relationships(
            conn,
            snapshot_id=snap,
            file_version_id=consumer.file_version.file_version_id,
        )
        # Exact path import of util.py.
        path_imports = [
            r
            for r in rels
            if r.relation_kind == "imports" and r.resolution_method == "relative_import_path"
        ]
        assert path_imports
        assert all(r.confidence == CONFIDENCE_EXACT for r in path_imports)

        # Imported alias ``do_help`` → util.helper (exact).
        refs = list_references_for_path(conn, snapshot_id=snap, relative_path="consumer.py")
        do_help = [r for r in refs if r.identifier == "do_help"]
        assert do_help
        alias_targets = list_targets_for_reference(
            conn, snapshot_id=snap, reference_id=do_help[0].reference_id
        )
        assert len(alias_targets) == 1
        assert alias_targets[0].is_preferred
        assert alias_targets[0].confidence == CONFIDENCE_EXACT
        assert alias_targets[0].resolution_method == "imported_alias"

        util_units = list_semantic_units_by_path(conn, snapshot_id=snap, relative_path="util.py")
        helper = next(u for u in util_units if u.unqualified_name == "helper")
        assert alias_targets[0].target_unit_id == helper.unit_id

        named_imports = [
            r
            for r in rels
            if r.relation_kind == "imports"
            and r.resolution_method == "exported_name"
            and r.target_unit_id == helper.unit_id
        ]
        assert named_imports
        assert all(r.confidence == CONFIDENCE_EXACT for r in named_imports)
    finally:
        conn.close()


def test_explicit_test_evidence_outranks_filename_affinity(tmp_path: Path) -> None:
    """AC5: import/call evidence yields stronger ``tests`` than filename-only."""
    repo = tmp_path / "repo"
    wt = repo / "wt"
    wt.mkdir(parents=True)
    materialize_fixture_repo("tests", wt)
    conn = open_context_index(repo, db_path=repo / "context-index.db")
    try:
        result = _index(repo, wt, conn, ts="2026-08-01T10:00:00Z")
        assert result.status == "ready"

        widget_test = get_file_version_by_path(
            conn, snapshot_id=result.snapshot_id, relative_path="test_widget.py"
        )
        assert widget_test is not None
        widget_rels = list_outgoing_relationships(
            conn,
            snapshot_id=result.snapshot_id,
            file_version_id=widget_test.file_version.file_version_id,
        )
        gadget_test = get_file_version_by_path(
            conn, snapshot_id=result.snapshot_id, relative_path="test_gadget.py"
        )
        assert gadget_test is not None
        gadget_rels = list_outgoing_relationships(
            conn,
            snapshot_id=result.snapshot_id,
            file_version_id=gadget_test.file_version.file_version_id,
        )

        widget_tests = [r for r in widget_rels if r.relation_kind == REL_TESTS]
        gadget_tests = [r for r in gadget_rels if r.relation_kind == REL_TESTS]

        # Filename-only affinity stays weak.
        assert gadget_tests
        assert all(r.confidence == CONFIDENCE_WEAK for r in gadget_tests)
        assert all(r.resolution_method == "test_filename_heuristic" for r in gadget_tests)

        # Explicit import/call of production → exact/inferred tests evidence.
        explicit = [
            r
            for r in widget_tests
            if r.resolution_method in {"test_import", "test_call", "test_render"}
            and r.confidence in {CONFIDENCE_EXACT, CONFIDENCE_INFERRED}
        ]
        assert explicit, "test_widget.py must emit explicit-evidence REL_TESTS"
        assert all(r.resolution_method != "test_filename_heuristic" for r in explicit)
        # Covered by explicit evidence — no weak filename duplicate for widget.
        assert not any(r.resolution_method == "test_filename_heuristic" for r in widget_tests)

        assert max(tier_rank(r.confidence) for r in widget_tests) > max(
            tier_rank(r.confidence) for r in gadget_tests
        )
    finally:
        conn.close()


def test_framework_fixtures_indexed_and_resolved(  # noqa: PLR0915
    tmp_path: Path,
) -> None:
    """Indexed + resolved path per framework — not extraction-only smoke."""
    registry = default_registry()

    # React: no lowercase HTML as components; ProfileCard resolves via index.
    react = (FIXTURES_ROOT / "frameworks" / "react" / "ProfileEditor.tsx").read_text()
    pipe = registry.select("ProfileEditor.tsx", source=react)
    assert pipe is not None
    result = pipe.extract("ProfileEditor.tsx", react)
    renders = [r for r in result.relationships if r.relation_kind == REL_RENDERS_COMPONENT]
    assert any("ProfileCard" in (r.target_qualified_name or "") for r in renders)
    assert not any((r.target_qualified_name or "").lower() in {"div", "span"} for r in renders)

    cases = (
        ("frameworks/react", "ProfileEditor.tsx", "ProfileCard"),
        ("frameworks/vue", "ProfileEditor.vue", "ProfileCard"),
        ("frameworks/svelte", "ProfileEditor.svelte", "ProfileCard"),
        ("frameworks/angular", "profile-editor.component.ts", "ProfileCardComponent"),
    )
    for shape, editor, target_name in cases:
        repo = tmp_path / shape.replace("/", "_")
        wt = repo / "wt"
        wt.mkdir(parents=True)
        materialize_fixture_repo(shape, wt)
        conn = open_context_index(repo, db_path=repo / "context-index.db")
        try:
            indexed = _index(repo, wt, conn, ts="2026-08-01T10:00:00Z")
            assert indexed.status == "ready", f"{shape}: {indexed.failure_reason}"
            editor_fv = get_file_version_by_path(
                conn, snapshot_id=indexed.snapshot_id, relative_path=editor
            )
            assert editor_fv is not None
            rels = list_outgoing_relationships(
                conn,
                snapshot_id=indexed.snapshot_id,
                file_version_id=editor_fv.file_version.file_version_id,
            )
            renders_resolved = [
                r
                for r in rels
                if r.relation_kind == REL_RENDERS_COMPONENT
                and r.resolution_method == "framework_selector"
            ]
            assert renders_resolved, f"{shape}: missing resolved renders_component"
            assert all(r.confidence == CONFIDENCE_INFERRED for r in renders_resolved)

            # Target unit must be the named component (not a lowercase HTML tag).
            hit = False
            for r in renders_resolved:
                assert r.target_unit_id is not None
                for path_candidate in (
                    "ProfileCard.tsx",
                    "ProfileCard.vue",
                    "ProfileCard.svelte",
                    "profile-card.component.ts",
                    "dup-card.component.ts",
                ):
                    units = list_semantic_units_by_path(
                        conn,
                        snapshot_id=indexed.snapshot_id,
                        relative_path=path_candidate,
                    )
                    if any(
                        u.unit_id == r.target_unit_id and u.unqualified_name == target_name
                        for u in units
                    ):
                        hit = True
                        break
            assert hit, f"{shape}: renders did not target {target_name}"

            if shape == "frameworks/react":
                # Lowercase HTML never becomes a semantic unit / render target.
                units = list_semantic_units_by_path(
                    conn,
                    snapshot_id=indexed.snapshot_id,
                    relative_path="ProfileEditor.tsx",
                )
                assert not any(u.unqualified_name.lower() in {"div", "span"} for u in units)

            if shape == "frameworks/vue":
                # Kebab-case template tag resolves to the PascalCase component.
                refs = list_references_for_path(
                    conn,
                    snapshot_id=indexed.snapshot_id,
                    relative_path=editor,
                )
                kebab_refs = [
                    r
                    for r in refs
                    if r.reference_kind == "component_tag"
                    and json.loads(r.metadata_json or "{}").get("tag") == "profile-card"
                ]
                assert kebab_refs, "vue fixture must promote <profile-card /> as component_tag"
                kebab_targets = list_targets_for_reference(
                    conn,
                    snapshot_id=indexed.snapshot_id,
                    reference_id=kebab_refs[0].reference_id,
                )
                assert kebab_targets
                card_units = list_semantic_units_by_path(
                    conn,
                    snapshot_id=indexed.snapshot_id,
                    relative_path="ProfileCard.vue",
                )
                card_id = next(u.unit_id for u in card_units if u.unqualified_name == "ProfileCard")
                assert any(t.target_unit_id == card_id for t in kebab_targets)
                kebab_line = kebab_refs[0].start_line
                assert any(
                    r.relation_kind == REL_RENDERS_COMPONENT
                    and r.start_line == kebab_line
                    and r.target_unit_id == card_id
                    and json.loads(r.metadata_json or "{}").get("tag") == "profile-card"
                    for r in rels
                ), "resolved renders_component must preserve kebab-case tag"
                # External <style src> → resource:style edge to ProfileEditor.css.
                css = get_file_version_by_path(
                    conn,
                    snapshot_id=indexed.snapshot_id,
                    relative_path="ProfileEditor.css",
                )
                assert css is not None
                style_edges = [
                    r
                    for r in rels
                    if r.relation_kind == "resource:style"
                    and r.resolution_method == "resource_path"
                ]
                assert style_edges
                assert any(r.target_file_id == css.file.file_id for r in style_edges)

            if shape == "frameworks/angular":
                # templateUrl / styleUrls resolved as resource edges.
                resources = [r for r in rels if r.relation_kind.startswith("resource:")]
                assert any(r.relation_kind == "resource:template" for r in resources)
                assert any(r.relation_kind == "resource:style" for r in resources)
                # Colliding selectors: both ProfileCard and DupCard.
                target_ids = {r.target_unit_id for r in renders_resolved}
                assert len(target_ids) >= 2  # noqa: PLR2004
        finally:
            conn.close()


def test_lower_precedence_never_overrides_exact_match(tmp_path: Path) -> None:
    """When imported-alias (exact) and unique-export (inferred) both hit the
    same unit, the persisted target keeps the exact-tier method.
    """
    repo = tmp_path / "repo"
    wt = repo / "wt"
    wt.mkdir(parents=True)
    (wt / "lib.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    (wt / "consumer.py").write_text(
        "from lib import helper\n\n\ndef run():\n    return helper()\n",
        encoding="utf-8",
    )
    conn = open_context_index(repo, db_path=repo / "context-index.db")
    try:
        result = _index(repo, wt, conn, ts="2026-08-01T10:00:00Z")
        assert result.status == "ready"
        refs = list_references_for_path(
            conn, snapshot_id=result.snapshot_id, relative_path="consumer.py"
        )
        helper_refs = [r for r in refs if r.identifier == "helper"]
        assert helper_refs
        targets = list_targets_for_reference(
            conn,
            snapshot_id=result.snapshot_id,
            reference_id=helper_refs[0].reference_id,
        )
        assert targets
        preferred = [t for t in targets if t.is_preferred]
        assert len(preferred) == 1
        assert preferred[0].confidence == CONFIDENCE_EXACT
        assert preferred[0].resolution_method == "imported_alias"
        # No weaker-tier row for the same unit may outrank / replace exact.
        lib_units = list_semantic_units_by_path(
            conn, snapshot_id=result.snapshot_id, relative_path="lib.py"
        )
        helper_id = next(u.unit_id for u in lib_units if u.unqualified_name == "helper")
        for t in targets:
            if t.target_unit_id == helper_id:
                assert t.confidence == CONFIDENCE_EXACT
                assert t.resolution_method != "unique_exported_name"
                assert t.resolution_method != "package_unqualified_name"
    finally:
        conn.close()


def test_renders_component_only_for_component_or_directive_targets(tmp_path: Path) -> None:
    """Ordinary symbol resolution must not emit renders_component edges."""
    repo = tmp_path / "repo"
    wt = repo / "wt"
    wt.mkdir(parents=True)
    materialize_fixture_repo("cross_file", wt)
    conn = open_context_index(repo, db_path=repo / "context-index.db")
    try:
        result = _index(repo, wt, conn, ts="2026-08-01T10:00:00Z")
        assert result.status == "ready"
        resolved = list_resolved_relationships_for_snapshot(conn, snapshot_id=result.snapshot_id)
        assert not any(r.relation_kind == REL_RENDERS_COMPONENT for r in resolved)
    finally:
        conn.close()

    # Framework path: every renders_component target is component/directive.
    vue_repo = tmp_path / "vue_repo"
    vue_wt = vue_repo / "wt"
    vue_wt.mkdir(parents=True)
    materialize_fixture_repo("frameworks/vue", vue_wt)
    vue_conn = open_context_index(vue_repo, db_path=vue_repo / "context-index.db")
    try:
        indexed = _index(vue_repo, vue_wt, vue_conn, ts="2026-08-01T10:00:00Z")
        assert indexed.status == "ready"
        editor = get_file_version_by_path(
            vue_conn, snapshot_id=indexed.snapshot_id, relative_path="ProfileEditor.vue"
        )
        assert editor is not None
        rels = list_outgoing_relationships(
            vue_conn,
            snapshot_id=indexed.snapshot_id,
            file_version_id=editor.file_version.file_version_id,
        )
        renders = [r for r in rels if r.relation_kind == REL_RENDERS_COMPONENT]
        assert renders
        role_by_unit: dict[int, str | None] = {}
        for path in ("ProfileCard.vue", "ProfileEditor.vue"):
            for unit in list_semantic_units_by_path(
                vue_conn, snapshot_id=indexed.snapshot_id, relative_path=path
            ):
                role_by_unit[unit.unit_id] = unit.semantic_role
        for edge in renders:
            assert edge.target_unit_id is not None
            assert role_by_unit.get(edge.target_unit_id) in {"component", "directive"}
    finally:
        vue_conn.close()


def test_eval_harness_forbidden_hits_are_detected() -> None:
    """Precision/noise scoring path: forbidden presence raises the hit count."""
    case = EvalCase(
        name="forbidden-detect",
        objective="x",
        profile=RecipientProfile.IMPLEMENTATION,
        fixture_shape="cross_file",
        expected=(UnitRef("util.py", "helper"),),
        forbidden=(UnitRef("noise.py", "distractor"),),
        top_k=5,
    )
    clean = (CandidateSnapshot("util.py", "helper", 1, 1, 2, "exact", ("hint",)),)
    noisy = (
        CandidateSnapshot("util.py", "helper", 1, 1, 2, "exact", ("hint",)),
        CandidateSnapshot("noise.py", "distractor", 2, 1, 2, "lexical", ("noise",)),
    )
    report_clean = _score_case(case, snapshots_a=clean, snapshots_b=clean)
    assert report_clean.forbidden_unit_hits == 0
    assert report_clean.expected_unit_recall == 1.0

    report_noisy = _score_case(case, snapshots_a=noisy, snapshots_b=noisy)
    assert report_noisy.forbidden_unit_hits == 1
    assert report_noisy.hit_forbidden == ("noise.py::distractor",)


def _case_report_identity(report: EvalCaseReport) -> tuple[object, ...]:
    """Stable multi-field identity — stronger than candidate-key JSON alone."""
    return (
        report.name,
        report.candidate_count,
        report.expected_unit_recall,
        report.top_k_recall,
        report.forbidden_unit_hits,
        report.provider_attribution,
        report.determinism_status,
        report.hit_expected,
        report.missed_expected,
        report.hit_forbidden,
    )


def test_eval_harness_reports_recall_deterministically(tmp_path: Path) -> None:
    cases = all_candidate_cases()
    shapes = {c.fixture_shape for c in cases}
    assert shapes >= {
        "cross_file",
        "unresolved",
        "tests",
        "snapshot_drift",
        "frameworks/react",
        "frameworks/vue",
        "frameworks/svelte",
        "frameworks/angular",
    }
    assert any(c.forbidden for c in cases), "candidate cases must exercise forbidden/noise"

    report = run_cases(cases, work_dir=tmp_path / "eval")
    assert report.all_deterministic
    for case_report in report.cases:
        # Report fields asserted together (Step 3 gate).
        assert case_report.determinism_status == "identical"
        assert case_report.candidate_count > 0
        assert case_report.expected_unit_recall == 1.0
        assert case_report.top_k_recall == 1.0
        assert case_report.missed_expected == ()
        assert case_report.forbidden_unit_hits == 0
        assert case_report.hit_forbidden == ()
        assert case_report.provider_attribution  # non-empty provider set

    # Replay: full report identity (not only intra-run candidate-key equality).
    report_b = run_cases(cases, work_dir=tmp_path / "eval_replay")
    assert tuple(_case_report_identity(c) for c in report.cases) == tuple(
        _case_report_identity(c) for c in report_b.cases
    )

    # Single-case path also deterministic.
    one = run_case(cases[0], work_dir=tmp_path / "one")
    assert one.determinism_status == "identical"
    assert one.expected_unit_recall == 1.0
    one_b = run_case(cases[0], work_dir=tmp_path / "one_replay")
    assert _case_report_identity(one) == _case_report_identity(one_b)


def test_run_cases_default_is_candidate_mode_not_corpus() -> None:
    """Step 3 entry: bare run_cases() selects candidate cases, not corpus/graded."""
    candidates = all_candidate_cases()
    corpus = all_corpus_cases()
    graded = all_graded_cases()
    assert candidates
    assert corpus
    assert graded
    assert all(c.mode == "candidates" for c in candidates)
    assert all(c.mode == "corpus" for c in corpus)
    assert all(c.mode == "graded" for c in graded)
    # Names must not silently mix — default path is the candidate suite.
    assert {c.name for c in candidates}.isdisjoint({c.name for c in corpus})
    assert {c.name for c in candidates}.isdisjoint({c.name for c in graded})
    assert {c.name for c in corpus}.isdisjoint({c.name for c in graded})
