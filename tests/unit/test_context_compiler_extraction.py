"""Focused tests for Context Assembler 2 Track A extraction foundation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from murder.context_compiler.extraction import (
    EXTRACTION_SCHEMA_VERSION,
    ExtractedRelationship,
    ExtractedSemanticUnit,
    ExtractionPipeline,
    ExtractorRegistry,
    FileExtraction,
    FrameworkEnricher,
    LanguageExtractor,
    assign_disambiguators,
    build_extractor_version,
    make_local_id,
    make_logical_key,
)
from murder.context_compiler.indexing import map_file_extraction
from murder.context_compiler.persistence import (
    create_building_snapshot,
    get_or_create_worktree,
    list_relationships_for_file_version,
    open_context_index,
    replace_file_extraction,
)


@dataclass(frozen=True, slots=True)
class _StubExtractor:
    extractor_id: str
    extractor_version: str
    _languages: tuple[str, ...] = ()
    _extensions: tuple[str, ...] = ()

    def supports(self, path: str, language_hint: str | None = None) -> bool:
        return False

    def extract(
        self,
        path: str,
        source: str,
        *,
        language_hint: str | None = None,
    ) -> FileExtraction:
        return FileExtraction(path=path, language=self.extractor_id, parse_status="parsed")


@dataclass(frozen=True, slots=True)
class _StubEnricher:
    enricher_id: str
    enricher_version: str
    _apply: bool = True

    def applies(
        self,
        path: str,
        source: str,
        *,
        language: str | None = None,
        language_hint: str | None = None,
    ) -> bool:
        return self._apply

    def enrich(
        self,
        extraction: FileExtraction,
        source: str,
        *,
        path: str,
    ) -> FileExtraction:
        return extraction


def test_local_id_is_deterministic_and_ignores_lines() -> None:
    a = make_local_id(language_kind="function", qualified_name="mod.foo")
    b = make_local_id(language_kind="function", qualified_name="mod.foo")
    assert a == b == "function:mod.foo"
    with_disambiguator = make_local_id(
        language_kind="function",
        qualified_name="mod.foo",
        disambiguator="overload-1",
    )
    assert with_disambiguator == "function:mod.foo:overload-1"


def test_logical_key_shape() -> None:
    key = make_logical_key(
        language="python",
        path="pkg/mod.py",
        qualified_name="pkg.mod.Foo.bar",
        language_kind="method",
        disambiguator="overload-2",
    )
    assert key == "python:pkg/mod.py:pkg.mod.Foo.bar:method:overload-2"


def test_assign_disambiguators_is_stable() -> None:
    units = (
        ExtractedSemanticUnit(
            local_id="function:mod.foo",
            language_kind="function",
            qualified_name="mod.foo",
            unqualified_name="foo",
            start_line=20,
            end_line=30,
        ),
        ExtractedSemanticUnit(
            local_id="function:mod.foo",
            language_kind="function",
            qualified_name="mod.foo",
            unqualified_name="foo",
            start_line=1,
            end_line=10,
        ),
    )
    out = assign_disambiguators(units)
    assert out[0].metadata["disambiguator"] == "overload-2"
    assert out[1].metadata["disambiguator"] == "overload-1"
    assert out[1].local_id == "function:mod.foo:overload-1"
    assert out[0].local_id == "function:mod.foo:overload-2"


def test_build_extractor_version() -> None:
    assert (
        build_extractor_version(EXTRACTION_SCHEMA_VERSION, "python-ast-1")
        == "schema-1:python-ast-1"
    )
    assert (
        build_extractor_version(
            EXTRACTION_SCHEMA_VERSION,
            "tree-sitter-typescript-1",
            "react-1",
        )
        == "schema-1:tree-sitter-typescript-1:react-1"
    )


def test_registry_selection_precedence_and_version() -> None:
    registry = ExtractorRegistry()
    py = _StubExtractor("python-ast", "python-ast-1")
    ts = _StubExtractor("tree-sitter-typescript", "tree-sitter-typescript-1")
    vue = _StubExtractor("vue-sfc", "vue-sfc-1")
    registry.register(py, languages=("python",), extensions=(".py",))
    registry.register(ts, languages=("typescript", "tsx"), extensions=(".ts", ".tsx"))
    registry.register(vue, languages=("vue",), extensions=(".vue",))

    react = _StubEnricher("react", "react-1")
    registry.register_enricher(react, languages=("typescript", "tsx", "javascript", "jsx"))

    assert registry.resolve_language("src/App.vue") == "vue"
    assert registry.resolve_language("src/a.ts") == "typescript"
    assert registry.resolve_language("x", language_hint="python") == "python"

    vue_pipe = registry.select("src/App.vue")
    assert vue_pipe is not None
    assert vue_pipe.base.extractor_id == "vue-sfc"
    assert vue_pipe.extractor_version == "schema-1:vue-sfc-1"
    assert vue_pipe.enrichers == ()

    ts_pipe = registry.select("src/a.tsx", source="export function App() { return <div/> }")
    assert ts_pipe is not None
    assert ts_pipe.base.extractor_id == "tree-sitter-typescript"
    assert [e.enricher_id for e in ts_pipe.enrichers] == ["react"]
    assert ts_pipe.extractor_version == "schema-1:tree-sitter-typescript-1:react-1"

    # Protocol structural checks
    assert isinstance(py, LanguageExtractor)
    assert isinstance(react, FrameworkEnricher)
    assert isinstance(ts_pipe, ExtractionPipeline)


def test_mapper_builds_logical_keys_and_leaves_unresolved_targets() -> None:
    extraction = FileExtraction(
        path="pkg/mod.py",
        language="python",
        parse_status="parsed",
        semantic_units=(
            ExtractedSemanticUnit(
                local_id="class:pkg.mod.Foo",
                language_kind="class",
                qualified_name="pkg.mod.Foo",
                unqualified_name="Foo",
                start_line=1,
                end_line=20,
                exported=True,
            ),
            ExtractedSemanticUnit(
                local_id="method:pkg.mod.Foo.bar",
                language_kind="method",
                qualified_name="pkg.mod.Foo.bar",
                unqualified_name="bar",
                start_line=5,
                end_line=10,
                parent_local_id="class:pkg.mod.Foo",
            ),
        ),
        relationships=(
            ExtractedRelationship(
                source_unit_local_id="class:pkg.mod.Foo",
                target_qualified_name="other.Base",
                target_path="pkg/other.py",
                relation_kind="inherits",
                confidence=0.5,
                resolution_method="local_name",
            ),
        ),
    )
    replacement = map_file_extraction(
        extraction,
        source_hash="abc",
        byte_count=10,
        line_count=20,
        extractor_version="schema-1:python-ast-1",
    )
    assert replacement.relative_path == "pkg/mod.py"
    assert replacement.units[0].logical_key == "python:pkg/mod.py:pkg.mod.Foo:class"
    assert replacement.units[1].parent_logical_key == replacement.units[0].logical_key
    assert replacement.relationships[0].target_unit_id is None
    assert replacement.relationships[0].target_file_id is None
    assert replacement.relationships[0].metadata is not None
    assert replacement.relationships[0].metadata["target_qualified_name"] == "other.Base"
    assert replacement.relationships[0].metadata["target_path"] == "pkg/other.py"


def test_mapper_same_file_relationship_gets_target_logical_key() -> None:
    """Within-file edges must carry target_logical_key for persist-time resolve."""
    extraction = FileExtraction(
        path="pkg/mod.py",
        language="python",
        parse_status="parsed",
        semantic_units=(
            ExtractedSemanticUnit(
                local_id="class:pkg.mod.Foo",
                language_kind="class",
                qualified_name="pkg.mod.Foo",
                unqualified_name="Foo",
                start_line=1,
                end_line=20,
            ),
            ExtractedSemanticUnit(
                local_id="method:pkg.mod.Foo.bar",
                language_kind="method",
                qualified_name="pkg.mod.Foo.bar",
                unqualified_name="bar",
                start_line=5,
                end_line=10,
                parent_local_id="class:pkg.mod.Foo",
            ),
        ),
        relationships=(
            ExtractedRelationship(
                source_unit_local_id="class:pkg.mod.Foo",
                target_local_id="method:pkg.mod.Foo.bar",
                target_qualified_name="pkg.mod.Foo.bar",
                relation_kind="contains",
                confidence=1.0,
                resolution_method="parent_local_id",
            ),
            # Export-style edges often omit target_local_id but name a local unit.
            ExtractedRelationship(
                source_unit_local_id="method:pkg.mod.Foo.bar",
                target_qualified_name="pkg.mod.Foo.bar",
                relation_kind="exports",
                confidence=1.0,
                resolution_method="export_keyword",
            ),
        ),
    )
    replacement = map_file_extraction(
        extraction,
        source_hash="abc",
        byte_count=10,
        line_count=20,
        extractor_version="schema-1:python-ast-1",
    )
    contains, exports = replacement.relationships
    assert contains.metadata is not None
    assert contains.metadata["target_logical_key"] == replacement.units[1].logical_key
    assert exports.metadata is not None
    assert exports.metadata["target_logical_key"] == replacement.units[1].logical_key


def test_replace_file_extraction_resolves_within_file_relationships(
    tmp_path: Path,
) -> None:
    """Persisting contains edges must not raise when targets are same-file only."""
    extraction = FileExtraction(
        path="pkg/mod.py",
        language="python",
        parse_status="parsed",
        semantic_units=(
            ExtractedSemanticUnit(
                local_id="class:pkg.mod.Foo",
                language_kind="class",
                qualified_name="pkg.mod.Foo",
                unqualified_name="Foo",
                start_line=1,
                end_line=20,
            ),
            ExtractedSemanticUnit(
                local_id="method:pkg.mod.Foo.bar",
                language_kind="method",
                qualified_name="pkg.mod.Foo.bar",
                unqualified_name="bar",
                start_line=5,
                end_line=10,
                parent_local_id="class:pkg.mod.Foo",
            ),
        ),
        relationships=(
            ExtractedRelationship(
                source_unit_local_id="class:pkg.mod.Foo",
                target_local_id="method:pkg.mod.Foo.bar",
                target_qualified_name="pkg.mod.Foo.bar",
                relation_kind="contains",
                confidence=1.0,
                resolution_method="parent_local_id",
            ),
            ExtractedRelationship(
                source_unit_local_id="class:pkg.mod.Foo",
                target_qualified_name="other.Base",
                target_path="pkg/other.py",
                relation_kind="inherits",
                confidence=0.5,
                resolution_method="local_name",
            ),
        ),
    )
    replacement = map_file_extraction(
        extraction,
        source_hash="abc123",
        byte_count=40,
        line_count=20,
        extractor_version="schema-1:python-ast-1",
    )

    conn = open_context_index(tmp_path, db_path=tmp_path / "context-index.db")
    try:
        wt = get_or_create_worktree(conn, repository_root=tmp_path, worktree_root=tmp_path)
        snap = create_building_snapshot(
            conn,
            worktree_id=wt.worktree_id,
            state_timestamp="2026-08-01T00:00:00Z",
            commit_sha=None,
            generated_at="2026-08-01T00:00:00Z",
        )
        version = replace_file_extraction(
            conn,
            snapshot_id=snap.snapshot_id,
            worktree_id=wt.worktree_id,
            extraction=replacement,
            seen_at="2026-08-01T00:00:00Z",
        )
        rels = list_relationships_for_file_version(conn, version.file_version_id)
        assert len(rels) == 1
        assert rels[0].relation_kind == "contains"
        assert rels[0].target_unit_id is not None
    finally:
        conn.close()
