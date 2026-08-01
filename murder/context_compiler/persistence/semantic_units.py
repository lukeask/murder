"""Persistence for semantic units and their per-version extraction data.

Two layers of identity are involved here:

* ``semantic_units`` is the *logical* identity of a code entity (a function,
  class, etc.) within a logical file. It is keyed on ``(file_id,
  logical_key)`` and deliberately excludes line numbers, so the same logical
  unit is reused across edits that move code around without changing its
  identity. ``logical_key`` is caller-defined (e.g. a qualified-name-derived
  string) and stable across versions.
* ``semantic_unit_versions`` is the *versioned* extraction of a unit as it
  appeared in one exact ``file_versions`` row, keyed on ``(unit_id,
  file_version_id)``. Unchanged files reuse the same file version and thus
  the same unit versions across snapshots.

``parent_unit_id`` lives on the *version* row rather than the logical unit
row because containment can change from one version to the next (a method
can be moved between classes, a helper can be nested or unnested) even
though the unit's own logical identity does not change.

``language_kind`` and ``semantic_role`` are kept separate: ``language_kind``
describes the syntactic construct as the source language defines it (e.g.
``"function"``, ``"class"``, ``"method"``), while ``semantic_role`` is an
optional, higher-level classification layered on top by extractors or later
analysis (e.g. ``"entry_point"``, ``"test"``, ``"handler"``) and may be
``None`` when no such classification applies.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from murder.context_compiler.persistence.records import (
    SemanticUnitRecord,
    SemanticUnitVersionRecord,
)


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def dump_metadata(metadata: dict[str, Any] | None) -> str:
    """Serialize a metadata dict to the canonical TEXT form stored on disk."""
    if not metadata:
        return "{}"
    return json.dumps(metadata, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _row_to_unit_record(row: sqlite3.Row) -> SemanticUnitRecord:
    return SemanticUnitRecord(
        unit_id=row["unit_id"],
        file_id=row["file_id"],
        logical_key=row["logical_key"],
        first_seen_at=row["first_seen_at"],
        last_seen_at=row["last_seen_at"],
    )


def _row_to_unit_version_record(row: sqlite3.Row) -> SemanticUnitVersionRecord:
    return SemanticUnitVersionRecord(
        unit_version_id=row["unit_version_id"],
        unit_id=row["unit_id"],
        file_version_id=row["file_version_id"],
        language_kind=row["language_kind"],
        semantic_role=row["semantic_role"],
        qualified_name=row["qualified_name"],
        unqualified_name=row["unqualified_name"],
        signature=row["signature"],
        start_line=row["start_line"],
        end_line=row["end_line"],
        parent_unit_id=row["parent_unit_id"],
        exported=bool(row["exported"]),
        metadata_json=row["metadata_json"],
    )


def get_or_create_semantic_unit(
    conn: sqlite3.Connection,
    *,
    file_id: int,
    logical_key: str,
    seen_at: str | None = None,
) -> SemanticUnitRecord:
    """Upsert the logical semantic unit for ``(file_id, logical_key)``.

    Updates ``last_seen_at`` on a hit; sets both timestamps on first insert.
    """
    now = seen_at if seen_at is not None else _now()
    conn.execute(
        """
        INSERT INTO semantic_units (file_id, logical_key, first_seen_at, last_seen_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(file_id, logical_key) DO UPDATE SET
            last_seen_at = excluded.last_seen_at
        """,
        (file_id, logical_key, now, now),
    )
    row = conn.execute(
        """
        SELECT unit_id, file_id, logical_key, first_seen_at, last_seen_at
          FROM semantic_units
         WHERE file_id = ? AND logical_key = ?
        """,
        (file_id, logical_key),
    ).fetchone()
    assert row is not None
    return _row_to_unit_record(row)


def upsert_semantic_unit_version(
    conn: sqlite3.Connection,
    *,
    unit_id: int,
    file_version_id: int,
    language_kind: str,
    qualified_name: str,
    unqualified_name: str,
    start_line: int,
    end_line: int,
    semantic_role: str | None = None,
    signature: str | None = None,
    parent_unit_id: int | None = None,
    exported: bool = False,
    metadata: dict[str, Any] | None = None,
) -> SemanticUnitVersionRecord:
    """Upsert the extraction of ``unit_id`` within ``file_version_id``.

    Raises ``ValueError`` if the unit's logical file does not match the file
    version's logical file (i.e. they don't belong to the same ``file_id``).
    """
    unit_row = conn.execute(
        "SELECT file_id FROM semantic_units WHERE unit_id = ?",
        (unit_id,),
    ).fetchone()
    if unit_row is None:
        raise ValueError(f"semantic unit {unit_id} does not exist")

    file_version_row = conn.execute(
        "SELECT file_id FROM file_versions WHERE file_version_id = ?",
        (file_version_id,),
    ).fetchone()
    if file_version_row is None:
        raise ValueError(f"file version {file_version_id} does not exist")

    if unit_row["file_id"] != file_version_row["file_id"]:
        raise ValueError(
            f"semantic unit {unit_id} belongs to file {unit_row['file_id']}, "
            f"but file version {file_version_id} belongs to file "
            f"{file_version_row['file_id']}"
        )

    metadata_json = dump_metadata(metadata)
    conn.execute(
        """
        INSERT INTO semantic_unit_versions (
            unit_id, file_version_id, language_kind, semantic_role,
            qualified_name, unqualified_name, signature, start_line, end_line,
            parent_unit_id, exported, metadata_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(unit_id, file_version_id) DO UPDATE SET
            language_kind = excluded.language_kind,
            semantic_role = excluded.semantic_role,
            qualified_name = excluded.qualified_name,
            unqualified_name = excluded.unqualified_name,
            signature = excluded.signature,
            start_line = excluded.start_line,
            end_line = excluded.end_line,
            parent_unit_id = excluded.parent_unit_id,
            exported = excluded.exported,
            metadata_json = excluded.metadata_json
        """,
        (
            unit_id,
            file_version_id,
            language_kind,
            semantic_role,
            qualified_name,
            unqualified_name,
            signature,
            start_line,
            end_line,
            parent_unit_id,
            1 if exported else 0,
            metadata_json,
        ),
    )
    row = conn.execute(
        """
        SELECT unit_version_id, unit_id, file_version_id, language_kind, semantic_role,
               qualified_name, unqualified_name, signature, start_line, end_line,
               parent_unit_id, exported, metadata_json
          FROM semantic_unit_versions
         WHERE unit_id = ? AND file_version_id = ?
        """,
        (unit_id, file_version_id),
    ).fetchone()
    assert row is not None
    return _row_to_unit_version_record(row)


def list_semantic_unit_versions_for_file_version(
    conn: sqlite3.Connection, file_version_id: int
) -> list[SemanticUnitVersionRecord]:
    """List all unit versions extracted for ``file_version_id``, in source order."""
    rows = conn.execute(
        """
        SELECT unit_version_id, unit_id, file_version_id, language_kind, semantic_role,
               qualified_name, unqualified_name, signature, start_line, end_line,
               parent_unit_id, exported, metadata_json
          FROM semantic_unit_versions
         WHERE file_version_id = ?
         ORDER BY start_line, unit_version_id
        """,
        (file_version_id,),
    ).fetchall()
    return [_row_to_unit_version_record(row) for row in rows]


def resolve_unit_version_in_snapshot(
    conn: sqlite3.Connection,
    *,
    unit_id: int,
    snapshot_id: int,
) -> SemanticUnitVersionRecord | None:
    """Find the version of ``unit_id`` present in ``snapshot_id``, if any.

    Joins through ``snapshot_files`` on ``file_version_id`` and requires the
    unit's logical file to match the snapshot file's logical file.
    """
    row = conn.execute(
        """
        SELECT suv.unit_version_id, suv.unit_id, suv.file_version_id, suv.language_kind,
               suv.semantic_role, suv.qualified_name, suv.unqualified_name,
               suv.signature, suv.start_line, suv.end_line, suv.parent_unit_id,
               suv.exported, suv.metadata_json
          FROM semantic_unit_versions AS suv
          JOIN snapshot_files AS sf ON sf.file_version_id = suv.file_version_id
          JOIN semantic_units AS su ON su.unit_id = suv.unit_id
         WHERE suv.unit_id = ?
           AND sf.snapshot_id = ?
           AND su.file_id = sf.file_id
        """,
        (unit_id, snapshot_id),
    ).fetchone()
    if row is None:
        return None
    return _row_to_unit_version_record(row)


def list_child_unit_versions_in_snapshot(
    conn: sqlite3.Connection,
    *,
    parent_unit_id: int,
    snapshot_id: int,
) -> list[SemanticUnitVersionRecord]:
    """List unit versions in ``snapshot_id`` whose ``parent_unit_id`` matches."""
    rows = conn.execute(
        """
        SELECT suv.unit_version_id, suv.unit_id, suv.file_version_id, suv.language_kind,
               suv.semantic_role, suv.qualified_name, suv.unqualified_name,
               suv.signature, suv.start_line, suv.end_line, suv.parent_unit_id,
               suv.exported, suv.metadata_json
          FROM semantic_unit_versions AS suv
          JOIN snapshot_files AS sf ON sf.file_version_id = suv.file_version_id
         WHERE suv.parent_unit_id = ?
           AND sf.snapshot_id = ?
         ORDER BY suv.start_line, suv.unit_version_id
        """,
        (parent_unit_id, snapshot_id),
    ).fetchall()
    return [_row_to_unit_version_record(row) for row in rows]


def get_semantic_unit(conn: sqlite3.Connection, unit_id: int) -> SemanticUnitRecord | None:
    """Fetch a logical semantic unit by id, or ``None`` if it doesn't exist."""
    row = conn.execute(
        """
        SELECT unit_id, file_id, logical_key, first_seen_at, last_seen_at
          FROM semantic_units
         WHERE unit_id = ?
        """,
        (unit_id,),
    ).fetchone()
    if row is None:
        return None
    return _row_to_unit_record(row)


def get_semantic_unit_version(
    conn: sqlite3.Connection, unit_version_id: int
) -> SemanticUnitVersionRecord | None:
    """Fetch a unit version by id, or ``None`` if it doesn't exist."""
    row = conn.execute(
        """
        SELECT unit_version_id, unit_id, file_version_id, language_kind, semantic_role,
               qualified_name, unqualified_name, signature, start_line, end_line,
               parent_unit_id, exported, metadata_json
          FROM semantic_unit_versions
         WHERE unit_version_id = ?
        """,
        (unit_version_id,),
    ).fetchone()
    if row is None:
        return None
    return _row_to_unit_version_record(row)


def delete_extraction_for_file_version(conn: sqlite3.Connection, file_version_id: int) -> None:
    """Delete unit versions extracted for ``file_version_id`` only.

    Leaves other versions of the same units, and the logical ``semantic_units``
    rows themselves, untouched. Dependent rows (imports, references,
    relationships, resource links) that reference the deleted unit versions
    are removed via ``ON DELETE CASCADE``.
    """
    conn.execute(
        "DELETE FROM semantic_unit_versions WHERE file_version_id = ?",
        (file_version_id,),
    )


__all__ = [
    "delete_extraction_for_file_version",
    "dump_metadata",
    "get_or_create_semantic_unit",
    "get_semantic_unit",
    "get_semantic_unit_version",
    "list_child_unit_versions_in_snapshot",
    "list_semantic_unit_versions_for_file_version",
    "resolve_unit_version_in_snapshot",
    "upsert_semantic_unit_version",
]
