"""Current-snapshot query helpers for candidate providers (Part 11).

All helpers operate against an explicit ``snapshot_id``. None select “latest”
by ``indexed_at`` or ``generated_at``.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from murder.context_compiler.indexing.resolution_policy import normalize_confidence
from murder.context_compiler.persistence.files import (
    get_file,
    get_file_version,
    list_snapshot_files,
    normalize_relative_path,
)
from murder.context_compiler.persistence.records import (
    CurrentPreviousSnapshots,
    FileRecord,
    FileVersionRecord,
    ImportRecord,
    ReferenceRecord,
    ReferenceTargetRecord,
    RelationshipRecord,
    ResourceLinkRecord,
    SemanticUnitVersionRecord,
    SnapshotFileRecord,
    SnapshotRecord,
)
from murder.context_compiler.persistence.relationships import (
    list_imports_for_file_version,
    list_reference_targets,
    list_references_for_file_version,
    list_relationships_for_file_version,
    list_resolved_reference_targets,
    list_resolved_relationships_for_snapshot,
    list_resource_links_for_file_version,
)
from murder.context_compiler.persistence.semantic_units import (
    list_semantic_unit_versions_for_file_version,
    resolve_unit_version_in_snapshot,
)
from murder.context_compiler.persistence.snapshots import (
    get_current_and_previous_ready,
    get_snapshot,
)


@dataclass(frozen=True, slots=True)
class SnapshotFileEntry:
    """File identity + selected version for one snapshot attachment."""

    file: FileRecord
    file_version: FileVersionRecord
    snapshot_file: SnapshotFileRecord


@dataclass(frozen=True, slots=True)
class FileHashComparison:
    """Current vs previous selected file hashes for one relative path."""

    path: str
    current_file_id: int | None
    previous_file_id: int | None
    current_source_hash: str | None
    previous_source_hash: str | None
    current_file_version_id: int | None
    previous_file_version_id: int | None

    @property
    def unchanged(self) -> bool:
        return (
            self.current_source_hash is not None
            and self.current_source_hash == self.previous_source_hash
        )

    @property
    def added(self) -> bool:
        return self.current_source_hash is not None and self.previous_source_hash is None

    @property
    def removed(self) -> bool:
        return self.current_source_hash is None and self.previous_source_hash is not None

    @property
    def changed(self) -> bool:
        return (
            self.current_source_hash is not None
            and self.previous_source_hash is not None
            and self.current_source_hash != self.previous_source_hash
        )


def get_ready_snapshots(conn: sqlite3.Connection, worktree_id: int) -> CurrentPreviousSnapshots:
    """Newest and second-newest ready snapshots (by state_timestamp)."""
    return get_current_and_previous_ready(conn, worktree_id)


def list_current_files(conn: sqlite3.Connection, snapshot_id: int) -> list[SnapshotFileEntry]:
    """List files attached to ``snapshot_id``, ordered by path."""
    entries: list[SnapshotFileEntry] = []
    for sf in list_snapshot_files(conn, snapshot_id):
        file_rec = get_file(conn, sf.file_id)
        version = get_file_version(conn, sf.file_version_id)
        if file_rec is None or version is None:
            continue
        entries.append(
            SnapshotFileEntry(
                file=file_rec,
                file_version=version,
                snapshot_file=sf,
            )
        )
    return entries


def get_file_version_by_path(
    conn: sqlite3.Connection,
    *,
    snapshot_id: int,
    relative_path: str,
) -> SnapshotFileEntry | None:
    """Resolve the file version selected for ``relative_path`` in ``snapshot_id``."""
    path = normalize_relative_path(relative_path)
    row = conn.execute(
        """
        SELECT sf.snapshot_id, sf.file_id, sf.file_version_id
          FROM snapshot_files sf
          JOIN files f ON f.file_id = sf.file_id
         WHERE sf.snapshot_id = ? AND f.path = ?
        """,
        (snapshot_id, path),
    ).fetchone()
    if row is None:
        return None
    file_rec = get_file(conn, int(row["file_id"]))
    version = get_file_version(conn, int(row["file_version_id"]))
    if file_rec is None or version is None:
        return None
    return SnapshotFileEntry(
        file=file_rec,
        file_version=version,
        snapshot_file=SnapshotFileRecord(
            snapshot_id=int(row["snapshot_id"]),
            file_id=int(row["file_id"]),
            file_version_id=int(row["file_version_id"]),
        ),
    )


def list_semantic_units_by_path(
    conn: sqlite3.Connection,
    *,
    snapshot_id: int,
    relative_path: str,
) -> list[SemanticUnitVersionRecord]:
    """Semantic unit versions for the selected file version of ``relative_path``."""
    entry = get_file_version_by_path(conn, snapshot_id=snapshot_id, relative_path=relative_path)
    if entry is None:
        return []
    return list_semantic_unit_versions_for_file_version(conn, entry.file_version.file_version_id)


def resolve_current_unit_version(
    conn: sqlite3.Connection,
    *,
    snapshot_id: int,
    unit_id: int,
) -> SemanticUnitVersionRecord | None:
    """Resolve logical ``unit_id`` to its version in ``snapshot_id``."""
    return resolve_unit_version_in_snapshot(conn, unit_id=unit_id, snapshot_id=snapshot_id)


def _unit_version_from_row(row: sqlite3.Row) -> SemanticUnitVersionRecord:
    return SemanticUnitVersionRecord(
        unit_version_id=int(row["unit_version_id"]),
        unit_id=int(row["unit_id"]),
        file_version_id=int(row["file_version_id"]),
        language_kind=str(row["language_kind"]),
        semantic_role=row["semantic_role"],
        qualified_name=str(row["qualified_name"]),
        unqualified_name=str(row["unqualified_name"]),
        signature=row["signature"],
        start_line=int(row["start_line"]),
        end_line=int(row["end_line"]),
        parent_unit_id=row["parent_unit_id"],
        exported=bool(row["exported"]),
        metadata_json=str(row["metadata_json"]),
    )


def search_units_by_name(
    conn: sqlite3.Connection,
    *,
    snapshot_id: int,
    name: str,
    qualified: bool | None = None,
    limit: int = 100,
) -> list[SemanticUnitVersionRecord]:
    """Search snapshot units by qualified and/or unqualified name.

    When ``qualified`` is ``True``, match ``qualified_name`` only.
    When ``False``, match ``unqualified_name`` only.
    When ``None``, match either.
    """
    if not name:
        return []
    limit = max(1, min(limit, 1000))
    if qualified is True:
        sql = """
            SELECT suv.*
              FROM semantic_unit_versions suv
              JOIN snapshot_files sf ON sf.file_version_id = suv.file_version_id
             WHERE sf.snapshot_id = ? AND suv.qualified_name = ?
             ORDER BY suv.qualified_name, suv.start_line, suv.unit_version_id
             LIMIT ?
        """
        params: tuple[object, ...] = (snapshot_id, name, limit)
    elif qualified is False:
        sql = """
            SELECT suv.*
              FROM semantic_unit_versions suv
              JOIN snapshot_files sf ON sf.file_version_id = suv.file_version_id
             WHERE sf.snapshot_id = ? AND suv.unqualified_name = ?
             ORDER BY suv.qualified_name, suv.start_line, suv.unit_version_id
             LIMIT ?
        """
        params = (snapshot_id, name, limit)
    else:
        sql = """
            SELECT suv.*
              FROM semantic_unit_versions suv
              JOIN snapshot_files sf ON sf.file_version_id = suv.file_version_id
             WHERE sf.snapshot_id = ?
               AND (suv.qualified_name = ? OR suv.unqualified_name = ?)
             ORDER BY suv.qualified_name, suv.start_line, suv.unit_version_id
             LIMIT ?
        """
        params = (snapshot_id, name, name, limit)
    rows = conn.execute(sql, params).fetchall()
    return [_unit_version_from_row(row) for row in rows]


def search_units_by_semantic_role(
    conn: sqlite3.Connection,
    *,
    snapshot_id: int,
    semantic_role: str,
    limit: int = 100,
) -> list[SemanticUnitVersionRecord]:
    """Search snapshot units by ``semantic_role``."""
    if not semantic_role:
        return []
    limit = max(1, min(limit, 1000))
    rows = conn.execute(
        """
        SELECT suv.*
          FROM semantic_unit_versions suv
          JOIN snapshot_files sf ON sf.file_version_id = suv.file_version_id
         WHERE sf.snapshot_id = ? AND suv.semantic_role = ?
         ORDER BY suv.qualified_name, suv.start_line, suv.unit_version_id
         LIMIT ?
        """,
        (snapshot_id, semantic_role, limit),
    ).fetchall()
    return [_unit_version_from_row(row) for row in rows]


def find_unit_containing_line(
    conn: sqlite3.Connection,
    *,
    snapshot_id: int,
    relative_path: str,
    line: int,
) -> SemanticUnitVersionRecord | None:
    """Smallest unit in the path's selected version whose range contains ``line``."""
    if line < 1:
        return None
    units = list_semantic_units_by_path(conn, snapshot_id=snapshot_id, relative_path=relative_path)
    containing = [u for u in units if u.start_line <= line <= u.end_line]
    if not containing:
        return None
    containing.sort(key=lambda u: (u.end_line - u.start_line, u.start_line, u.unit_version_id))
    return containing[0]


def list_outgoing_relationships(
    conn: sqlite3.Connection,
    *,
    snapshot_id: int,
    unit_version_id: int | None = None,
    file_version_id: int | None = None,
) -> list[RelationshipRecord]:
    """Outgoing relationships from a unit/file version in the snapshot.

    Merges local extraction edges (reusable with the file version) with
    snapshot-scoped resolved edges. Both require the source file version to
    be attached to ``snapshot_id``.
    """
    if unit_version_id is None and file_version_id is None:
        raise ValueError("unit_version_id or file_version_id is required")

    resolved_file_version_id = file_version_id
    if resolved_file_version_id is None:
        assert unit_version_id is not None
        row = conn.execute(
            """
            SELECT suv.file_version_id
              FROM semantic_unit_versions suv
              JOIN snapshot_files sf ON sf.file_version_id = suv.file_version_id
             WHERE sf.snapshot_id = ? AND suv.unit_version_id = ?
            """,
            (snapshot_id, unit_version_id),
        ).fetchone()
        if row is None:
            return []
        resolved_file_version_id = int(row["file_version_id"])
    else:
        row = conn.execute(
            """
            SELECT 1 FROM snapshot_files
             WHERE snapshot_id = ? AND file_version_id = ?
            """,
            (snapshot_id, resolved_file_version_id),
        ).fetchone()
        if row is None:
            return []

    local = list_relationships_for_file_version(conn, resolved_file_version_id)
    resolved = list_resolved_relationships_for_snapshot(
        conn,
        snapshot_id=snapshot_id,
        source_file_version_id=resolved_file_version_id,
    )
    merged = local + resolved
    if unit_version_id is not None:
        merged = [r for r in merged if r.source_unit_version_id == unit_version_id]
    merged.sort(
        key=lambda r: (
            0 if r.snapshot_id is None else 1,
            r.relationship_id,
        )
    )
    return merged


def list_incoming_relationships(
    conn: sqlite3.Connection,
    *,
    snapshot_id: int,
    target_unit_id: int | None = None,
    target_file_id: int | None = None,
) -> list[RelationshipRecord]:
    """Incoming relationships targeting a logical unit or file within the snapshot.

    Merges local and snapshot-scoped resolved edges whose source file version
    is attached to ``snapshot_id``.
    """
    if target_unit_id is None and target_file_id is None:
        raise ValueError("target_unit_id or target_file_id is required")

    clauses: list[str] = ["sf.snapshot_id = ?"]
    params: list[object] = [snapshot_id]
    if target_unit_id is not None:
        clauses.append("r.target_unit_id = ?")
        params.append(target_unit_id)
    if target_file_id is not None:
        clauses.append("r.target_file_id = ?")
        params.append(target_file_id)
    where = " AND ".join(clauses)

    local_rows = conn.execute(
        f"""
        SELECT r.relationship_id, r.source_file_version_id, r.source_unit_version_id,
               r.target_file_id, r.target_unit_id, r.relation_kind, r.start_line,
               r.end_line, r.confidence, r.resolution_method, r.metadata_json
          FROM relationships r
          JOIN snapshot_files sf ON sf.file_version_id = r.source_file_version_id
         WHERE {where}
         ORDER BY r.relationship_id
        """,
        tuple(params),
    ).fetchall()

    resolved_clauses = ["r.snapshot_id = ?", "sf.snapshot_id = ?"]
    resolved_params: list[object] = [snapshot_id, snapshot_id]
    if target_unit_id is not None:
        resolved_clauses.append("r.target_unit_id = ?")
        resolved_params.append(target_unit_id)
    if target_file_id is not None:
        resolved_clauses.append("r.target_file_id = ?")
        resolved_params.append(target_file_id)
    resolved_where = " AND ".join(resolved_clauses)

    resolved_rows = conn.execute(
        f"""
        SELECT r.relationship_id, r.snapshot_id, r.source_file_version_id,
               r.source_unit_version_id, r.target_file_id, r.target_unit_id,
               r.relation_kind, r.start_line, r.end_line, r.confidence,
               r.resolution_method, r.metadata_json
          FROM resolved_relationships r
          JOIN snapshot_files sf ON sf.file_version_id = r.source_file_version_id
         WHERE {resolved_where}
         ORDER BY r.relationship_id
        """,
        tuple(resolved_params),
    ).fetchall()

    results: list[RelationshipRecord] = []
    for row in local_rows:
        results.append(
            RelationshipRecord(
                relationship_id=int(row["relationship_id"]),
                source_file_version_id=int(row["source_file_version_id"]),
                source_unit_version_id=row["source_unit_version_id"],
                target_file_id=row["target_file_id"],
                target_unit_id=row["target_unit_id"],
                relation_kind=str(row["relation_kind"]),
                start_line=row["start_line"],
                end_line=row["end_line"],
                confidence=normalize_confidence(str(row["confidence"])),
                resolution_method=str(row["resolution_method"]),
                metadata_json=str(row["metadata_json"]),
                snapshot_id=None,
            )
        )
    for row in resolved_rows:
        results.append(
            RelationshipRecord(
                relationship_id=int(row["relationship_id"]),
                source_file_version_id=int(row["source_file_version_id"]),
                source_unit_version_id=row["source_unit_version_id"],
                target_file_id=row["target_file_id"],
                target_unit_id=row["target_unit_id"],
                relation_kind=str(row["relation_kind"]),
                start_line=row["start_line"],
                end_line=row["end_line"],
                confidence=normalize_confidence(str(row["confidence"])),
                resolution_method=str(row["resolution_method"]),
                metadata_json=str(row["metadata_json"]),
                snapshot_id=int(row["snapshot_id"]),
            )
        )
    results.sort(key=lambda r: (0 if r.snapshot_id is None else 1, r.relationship_id))
    return results


def list_targets_for_reference(
    conn: sqlite3.Connection,
    *,
    snapshot_id: int,
    reference_id: int,
) -> list[ReferenceTargetRecord]:
    """Resolved cross-file targets for ``reference_id`` in ``snapshot_id``.

    Local extraction-time targets (same-file) are included when present; they
    carry ``snapshot_id=None``. Cross-file targets always require the explicit
    snapshot — there is no "latest indexed_at" fallback.
    """
    local = list_reference_targets(conn, reference_id)
    resolved = list_resolved_reference_targets(
        conn, snapshot_id=snapshot_id, reference_id=reference_id
    )
    return local + resolved


def list_resource_links_for_path(
    conn: sqlite3.Connection,
    *,
    snapshot_id: int,
    relative_path: str,
) -> list[ResourceLinkRecord]:
    """Resource links from the selected file version of ``relative_path``."""
    entry = get_file_version_by_path(conn, snapshot_id=snapshot_id, relative_path=relative_path)
    if entry is None:
        return []
    return list_resource_links_for_file_version(conn, entry.file_version.file_version_id)


def list_imports_for_path(
    conn: sqlite3.Connection,
    *,
    snapshot_id: int,
    relative_path: str,
) -> list[ImportRecord]:
    entry = get_file_version_by_path(conn, snapshot_id=snapshot_id, relative_path=relative_path)
    if entry is None:
        return []
    return list_imports_for_file_version(conn, entry.file_version.file_version_id)


def list_references_for_path(
    conn: sqlite3.Connection,
    *,
    snapshot_id: int,
    relative_path: str,
) -> list[ReferenceRecord]:
    entry = get_file_version_by_path(conn, snapshot_id=snapshot_id, relative_path=relative_path)
    if entry is None:
        return []
    return list_references_for_file_version(conn, entry.file_version.file_version_id)


def compare_file_hashes(
    conn: sqlite3.Connection,
    *,
    current_snapshot_id: int,
    previous_snapshot_id: int | None,
) -> list[FileHashComparison]:
    """Compare selected file hashes between current and previous snapshots."""
    current = {e.file.path: e for e in list_current_files(conn, current_snapshot_id)}
    previous: dict[str, SnapshotFileEntry] = {}
    if previous_snapshot_id is not None:
        previous = {e.file.path: e for e in list_current_files(conn, previous_snapshot_id)}

    paths = sorted(set(current) | set(previous))
    results: list[FileHashComparison] = []
    for path in paths:
        cur = current.get(path)
        prev = previous.get(path)
        results.append(
            FileHashComparison(
                path=path,
                current_file_id=cur.file.file_id if cur else None,
                previous_file_id=prev.file.file_id if prev else None,
                current_source_hash=cur.file_version.source_hash if cur else None,
                previous_source_hash=prev.file_version.source_hash if prev else None,
                current_file_version_id=(cur.file_version.file_version_id if cur else None),
                previous_file_version_id=(prev.file_version.file_version_id if prev else None),
            )
        )
    return results


def compare_current_and_previous_hashes(
    conn: sqlite3.Connection,
    worktree_id: int,
) -> list[FileHashComparison]:
    """Compare newest vs second-newest ready snapshot file hashes."""
    pair = get_current_and_previous_ready(conn, worktree_id)
    if pair.current is None:
        return []
    previous_id = pair.previous.snapshot_id if pair.previous else None
    return compare_file_hashes(
        conn,
        current_snapshot_id=pair.current.snapshot_id,
        previous_snapshot_id=previous_id,
    )


def get_snapshot_record(conn: sqlite3.Connection, snapshot_id: int) -> SnapshotRecord | None:
    """Fetch a snapshot by id (any status)."""
    return get_snapshot(conn, snapshot_id)


__all__ = [
    "FileHashComparison",
    "SnapshotFileEntry",
    "compare_current_and_previous_hashes",
    "compare_file_hashes",
    "find_unit_containing_line",
    "get_file_version_by_path",
    "get_ready_snapshots",
    "get_snapshot_record",
    "list_current_files",
    "list_imports_for_path",
    "list_incoming_relationships",
    "list_outgoing_relationships",
    "list_references_for_path",
    "list_resource_links_for_path",
    "list_semantic_units_by_path",
    "list_targets_for_reference",
    "resolve_current_unit_version",
    "search_units_by_name",
    "search_units_by_semantic_role",
]
