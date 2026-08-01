"""Focused invariants for the incremental indexing coordinator."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from murder.context_compiler.extraction.models import (
    ExtractedSemanticUnit,
    FileExtraction,
)
from murder.context_compiler.extraction.registry import (
    ExtractorRegistry,
    reset_default_registry,
)
from murder.context_compiler.indexing import (
    get_file_version_by_path,
    index_worktree_sync,
    list_semantic_units_by_path,
    resolve_current_unit_version,
    search_units_by_name,
)
from murder.context_compiler.indexing.queries import list_current_files
from murder.context_compiler.persistence import (
    get_current_and_previous_ready,
    list_semantic_unit_versions_for_file_version,
    open_context_index,
)
from murder.context_compiler.persistence.files import get_file_version


@dataclass(frozen=True, slots=True)
class _CountingExtractor:
    """Deterministic stub extractor with a mutable call counter."""

    extractor_id: str
    extractor_version: str
    calls: list[str]

    def supports(self, path: str, language_hint: str | None = None) -> bool:
        return path.endswith(".py")

    def extract(
        self,
        path: str,
        source: str,
        *,
        language_hint: str | None = None,
    ) -> FileExtraction:
        self.calls.append(path)
        # One unit whose identity is stable across whitespace-preserving edits
        # of surrounding comments — qualified name from basename.
        stem = Path(path).stem
        unit = ExtractedSemanticUnit(
            local_id=f"function:{stem}.main",
            language_kind="function",
            qualified_name=f"{stem}.main",
            unqualified_name="main",
            start_line=1,
            end_line=max(1, source.count("\n") or 1),
            exported=True,
            semantic_role="entry_point",
        )
        return FileExtraction(
            path=path,
            language="python",
            parse_status="parsed",
            semantic_units=(unit,),
        )


@pytest.fixture()
def registry_with_stub(tmp_path: Path):
    reset_default_registry()
    reg = ExtractorRegistry()
    calls: list[str] = []
    stub = _CountingExtractor(
        extractor_id="python-stub",
        extractor_version="python-stub-1",
        calls=calls,
    )
    reg.register(stub, languages=("python",), extensions=(".py",), priority=10)
    yield reg, calls
    reset_default_registry()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_file_version_reused_across_snapshots(tmp_path: Path, registry_with_stub) -> None:
    registry, calls = registry_with_stub
    repo = tmp_path / "repo"
    wt = repo / "wt"
    _write(wt / "pkg" / "mod.py", "def main():\n    return 1\n")

    conn = open_context_index(repo, db_path=repo / ".murder" / "context-index.db")
    try:
        r1 = index_worktree_sync(
            repo,
            wt,
            state_timestamp="2026-08-01T10:00:00",
            commit_sha="aaa",
            registry=registry,
            conn=conn,
        )
        assert r1.status == "ready"
        assert r1.files_parsed == 1
        assert r1.files_reused == 0
        assert len(calls) == 1

        entry1 = get_file_version_by_path(
            conn, snapshot_id=r1.snapshot_id, relative_path="pkg/mod.py"
        )
        assert entry1 is not None
        v1 = entry1.file_version.file_version_id

        r2 = index_worktree_sync(
            repo,
            wt,
            state_timestamp="2026-08-01T11:00:00",
            commit_sha="bbb",
            registry=registry,
            conn=conn,
        )
        assert r2.status == "ready"
        assert r2.files_reused == 1
        assert r2.files_parsed == 0
        assert len(calls) == 1  # extractor not re-invoked

        entry2 = get_file_version_by_path(
            conn, snapshot_id=r2.snapshot_id, relative_path="pkg/mod.py"
        )
        assert entry2 is not None
        assert entry2.file_version.file_version_id == v1

        # Graph rows must still be present (replace was not called on reuse).
        units = list_semantic_unit_versions_for_file_version(conn, v1)
        assert len(units) == 1
        assert units[0].unqualified_name == "main"
    finally:
        conn.close()


def test_extractor_version_invalidates_reuse(tmp_path: Path, registry_with_stub) -> None:
    registry, calls = registry_with_stub
    repo = tmp_path / "repo"
    wt = repo / "wt"
    _write(wt / "pkg" / "mod.py", "def main():\n    return 1\n")

    conn = open_context_index(repo, db_path=repo / ".murder" / "context-index.db")
    try:
        r1 = index_worktree_sync(
            repo,
            wt,
            state_timestamp="2026-08-01T10:00:00",
            registry=registry,
            conn=conn,
        )
        assert r1.files_parsed == 1
        v1 = get_file_version_by_path(conn, snapshot_id=r1.snapshot_id, relative_path="pkg/mod.py")
        assert v1 is not None

        # Bump extractor version → must reparse even with identical source hash.
        registry.clear()
        calls2: list[str] = []
        stub2 = _CountingExtractor(
            extractor_id="python-stub",
            extractor_version="python-stub-2",
            calls=calls2,
        )
        registry.register(stub2, languages=("python",), extensions=(".py",), priority=10)

        r2 = index_worktree_sync(
            repo,
            wt,
            state_timestamp="2026-08-01T12:00:00",
            registry=registry,
            conn=conn,
        )
        assert r2.status == "ready"
        assert r2.files_parsed == 1
        assert r2.files_reused == 0
        assert len(calls2) == 1

        v2 = get_file_version_by_path(conn, snapshot_id=r2.snapshot_id, relative_path="pkg/mod.py")
        assert v2 is not None
        assert v2.file_version.file_version_id != v1.file_version.file_version_id
        assert v2.file_version.extractor_version.endswith("python-stub-2")
    finally:
        conn.close()


def test_snapshot_scoped_queries_do_not_leak_prior_versions(
    tmp_path: Path, registry_with_stub
) -> None:
    registry, _calls = registry_with_stub
    repo = tmp_path / "repo"
    wt = repo / "wt"
    _write(wt / "pkg" / "mod.py", "def main():\n    return 1\n")

    conn = open_context_index(repo, db_path=repo / ".murder" / "context-index.db")
    try:
        r1 = index_worktree_sync(
            repo,
            wt,
            state_timestamp="2026-08-01T10:00:00",
            registry=registry,
            conn=conn,
        )
        units_v1 = list_semantic_units_by_path(
            conn, snapshot_id=r1.snapshot_id, relative_path="pkg/mod.py"
        )
        assert len(units_v1) == 1
        unit_id = units_v1[0].unit_id
        old_end = units_v1[0].end_line

        # Change source so a new file version is written.
        _write(wt / "pkg" / "mod.py", "def main():\n    return 1\n    # pad\n    # pad\n")

        r2 = index_worktree_sync(
            repo,
            wt,
            state_timestamp="2026-08-01T13:00:00",
            registry=registry,
            conn=conn,
        )
        assert r2.files_parsed == 1

        # Current snapshot sees the new ranges.
        units_v2 = list_semantic_units_by_path(
            conn, snapshot_id=r2.snapshot_id, relative_path="pkg/mod.py"
        )
        assert len(units_v2) == 1
        assert units_v2[0].end_line != old_end
        assert units_v2[0].unit_id == unit_id  # logical unit preserved

        # Previous snapshot still resolves to the old unit version.
        resolved_old = resolve_current_unit_version(
            conn, snapshot_id=r1.snapshot_id, unit_id=unit_id
        )
        resolved_new = resolve_current_unit_version(
            conn, snapshot_id=r2.snapshot_id, unit_id=unit_id
        )
        assert resolved_old is not None and resolved_new is not None
        assert resolved_old.unit_version_id != resolved_new.unit_version_id
        assert resolved_old.end_line == old_end
        assert resolved_new.end_line == units_v2[0].end_line

        # Name search against snapshot 1 must not return snapshot 2's version.
        found_old = search_units_by_name(
            conn, snapshot_id=r1.snapshot_id, name="main", qualified=False
        )
        assert len(found_old) == 1
        assert found_old[0].unit_version_id == resolved_old.unit_version_id

        found_new = search_units_by_name(
            conn, snapshot_id=r2.snapshot_id, name="main", qualified=False
        )
        assert len(found_new) == 1
        assert found_new[0].unit_version_id == resolved_new.unit_version_id

        pair = get_current_and_previous_ready(conn, r2.worktree_id)
        assert pair.current is not None and pair.previous is not None
        assert pair.current.snapshot_id == r2.snapshot_id
        assert pair.previous.snapshot_id == r1.snapshot_id

        # Listing files on previous must still point at the old file version.
        prev_files = list_current_files(conn, r1.snapshot_id)
        curr_files = list_current_files(conn, r2.snapshot_id)
        assert len(prev_files) == 1 and len(curr_files) == 1
        assert (
            prev_files[0].file_version.file_version_id != curr_files[0].file_version.file_version_id
        )
        # Old version row still intact.
        assert get_file_version(conn, prev_files[0].file_version.file_version_id) is not None
    finally:
        conn.close()


def test_text_only_when_no_extractor(tmp_path: Path) -> None:
    reset_default_registry()
    registry = ExtractorRegistry()  # empty — no language extractors
    repo = tmp_path / "repo"
    wt = repo / "wt"
    _write(wt / "notes.md", "# hello\n")
    _write(wt / "pkg" / "mod.py", "def main():\n    pass\n")

    conn = open_context_index(repo, db_path=repo / ".murder" / "context-index.db")
    try:
        result = index_worktree_sync(
            repo,
            wt,
            state_timestamp="2026-08-01T10:00:00",
            registry=registry,
            conn=conn,
        )
        assert result.status == "ready"
        # .py has a known language extension but no registered extractor →
        # classified text_only (or unsupported path through indexable miss).
        assert result.files_parsed == 0
        assert result.files_text_only + result.files_unsupported >= 1
        files = list_current_files(conn, result.snapshot_id)
        paths = {f.file.path for f in files}
        assert "pkg/mod.py" in paths
    finally:
        conn.close()
        reset_default_registry()
