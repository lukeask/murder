"""Files, file versions, and snapshot attachment for the context index.

Two identities matter here, and they live at different lifetimes:

* A **file** (``files``) is a logical identity — a worktree-relative path.
  It is stable across snapshots: the same path always resolves to the same
  ``file_id``, and re-seeing it just refreshes ``last_seen_at``.
* A **file version** (``file_versions``) is a specific, exact-content
  extraction of that file — keyed by ``(file_id, source_hash,
  extractor_version)``. Source bodies are never stored; only the hash and
  the structural facts the extractor derived (imports, references,
  relationships, resource links, semantic units) live in the index. Final
  evidence text is always re-read from the live worktree.

Because file versions are keyed by content hash, a file that is edited and
then reverted — or one that is simply unchanged between two snapshots —
resolves to the *same* ``file_version_id`` and therefore reuses the same
child extraction rows. Snapshots attach to file versions (``snapshot_files``)
rather than owning copies of the extraction, so unchanged content is never
re-derived or duplicated across snapshots.

:func:`replace_file_extraction` is the single atomic entry point for
recording (or re-recording) everything an extractor produced for one file
version: it clears and repopulates that version's own child rows only,
never touching sibling file versions, and attaches the result to a
snapshot in the same transaction.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import cast

from murder.context_compiler.persistence.connection import transaction
from murder.context_compiler.persistence.records import (
    FileExtractionReplacement,
    FileRecord,
    FileVersionRecord,
    ParseStatus,
    SnapshotFileRecord,
)
from murder.context_compiler.persistence.relationships import (
    clear_file_version_graph_rows,
    insert_import,
    insert_reference,
    insert_reference_target,
    insert_relationship,
    insert_resource_link,
)
from murder.context_compiler.persistence.semantic_units import (
    delete_extraction_for_file_version,
    get_or_create_semantic_unit,
    upsert_semantic_unit_version,
)


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def normalize_relative_path(path: str) -> str:
    """Normalize ``path`` to a posix-style, worktree-relative path.

    Strips a leading ``./`` and collapses internal ``.``/empty segments.
    Raises :class:`ValueError` for absolute paths (posix or Windows-drive
    style) and for paths containing a ``..`` segment.
    """
    raw = path.strip()
    if not raw:
        raise ValueError("relative path must not be empty")

    normalized = raw.replace("\\", "/")
    drive, _, drive_rest = normalized.partition(":/")
    is_drive_absolute = bool(drive_rest) and len(drive) == 1 and drive.isalpha()
    if normalized.startswith("/") or is_drive_absolute:
        raise ValueError(f"path must be worktree-relative, got absolute path: {path!r}")

    segments = normalized.split("/")
    cleaned: list[str] = []
    for segment in segments:
        if segment in ("", "."):
            continue
        if segment == "..":
            raise ValueError(f"path must not contain '..' segments: {path!r}")
        cleaned.append(segment)

    if not cleaned:
        raise ValueError(f"path resolves to empty: {path!r}")
    return "/".join(cleaned)


def _file_from_row(row: sqlite3.Row) -> FileRecord:
    return FileRecord(
        file_id=int(row["file_id"]),
        worktree_id=int(row["worktree_id"]),
        path=str(row["path"]),
        first_seen_at=str(row["first_seen_at"]),
        last_seen_at=str(row["last_seen_at"]),
    )


def _file_version_from_row(row: sqlite3.Row) -> FileVersionRecord:
    return FileVersionRecord(
        file_version_id=int(row["file_version_id"]),
        file_id=int(row["file_id"]),
        source_hash=str(row["source_hash"]),
        language=cast("str | None", row["language"]),
        byte_count=int(row["byte_count"]),
        line_count=int(row["line_count"]),
        parse_status=cast(ParseStatus, row["parse_status"]),
        parse_error=cast("str | None", row["parse_error"]),
        extractor_version=str(row["extractor_version"]),
        indexed_at=str(row["indexed_at"]),
    )


def _snapshot_file_from_row(row: sqlite3.Row) -> SnapshotFileRecord:
    return SnapshotFileRecord(
        snapshot_id=int(row["snapshot_id"]),
        file_id=int(row["file_id"]),
        file_version_id=int(row["file_version_id"]),
    )


def get_or_create_file(
    conn: sqlite3.Connection,
    *,
    worktree_id: int,
    relative_path: str,
    seen_at: str | None = None,
) -> FileRecord:
    """Upsert the logical file row for ``relative_path``.

    ``relative_path`` is normalized via :func:`normalize_relative_path`
    first. Re-seeing an existing path only refreshes ``last_seen_at``;
    ``first_seen_at`` is preserved from the original insert.
    """
    normalized = normalize_relative_path(relative_path)
    now = seen_at if seen_at is not None else _now()
    conn.execute(
        """
        INSERT INTO files (worktree_id, path, first_seen_at, last_seen_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(worktree_id, path) DO UPDATE SET
            last_seen_at = excluded.last_seen_at
        """,
        (worktree_id, normalized, now, now),
    )
    row = conn.execute(
        "SELECT * FROM files WHERE worktree_id = ? AND path = ?",
        (worktree_id, normalized),
    ).fetchone()
    assert row is not None
    return _file_from_row(row)


def get_or_create_file_version(
    conn: sqlite3.Connection,
    *,
    file_id: int,
    source_hash: str,
    byte_count: int,
    line_count: int,
    parse_status: ParseStatus,
    extractor_version: str,
    language: str | None = None,
    parse_error: str | None = None,
    indexed_at: str | None = None,
) -> FileVersionRecord:
    """Upsert the content-addressed version row for ``(file_id, source_hash,
    extractor_version)``.

    On conflict, the existing row is returned unchanged — this is the
    mechanism by which unchanged content reuses its prior extraction
    children rather than triggering a fresh replace.
    """
    now = indexed_at if indexed_at is not None else _now()
    conn.execute(
        """
        INSERT INTO file_versions
            (file_id, source_hash, language, byte_count, line_count,
             parse_status, parse_error, extractor_version, indexed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(file_id, source_hash, extractor_version) DO NOTHING
        """,
        (
            file_id,
            source_hash,
            language,
            byte_count,
            line_count,
            parse_status,
            parse_error,
            extractor_version,
            now,
        ),
    )
    row = conn.execute(
        """
        SELECT * FROM file_versions
         WHERE file_id = ? AND source_hash = ? AND extractor_version = ?
        """,
        (file_id, source_hash, extractor_version),
    ).fetchone()
    assert row is not None
    return _file_version_from_row(row)


def attach_file_to_snapshot(
    conn: sqlite3.Connection,
    *,
    snapshot_id: int,
    file_id: int,
    file_version_id: int,
) -> SnapshotFileRecord:
    """Attach ``file_version_id`` to ``snapshot_id`` for ``file_id``.

    ``snapshot_files`` is keyed ``PRIMARY KEY (snapshot_id, file_id)``, so a
    second attach for the same ``(snapshot_id, file_id)`` updates the
    version pointer in place rather than erroring.
    """
    version = get_file_version(conn, file_version_id)
    if version is None or version.file_id != file_id:
        raise ValueError(f"file_version {file_version_id} does not belong to file {file_id}")
    conn.execute(
        """
        INSERT INTO snapshot_files (snapshot_id, file_id, file_version_id)
        VALUES (?, ?, ?)
        ON CONFLICT(snapshot_id, file_id) DO UPDATE SET
            file_version_id = excluded.file_version_id
        """,
        (snapshot_id, file_id, file_version_id),
    )
    return SnapshotFileRecord(
        snapshot_id=snapshot_id,
        file_id=file_id,
        file_version_id=file_version_id,
    )


def get_snapshot_file_version(
    conn: sqlite3.Connection,
    *,
    snapshot_id: int,
    file_id: int,
) -> FileVersionRecord | None:
    row = conn.execute(
        """
        SELECT fv.*
          FROM snapshot_files sf
          JOIN file_versions fv ON fv.file_version_id = sf.file_version_id
         WHERE sf.snapshot_id = ? AND sf.file_id = ?
        """,
        (snapshot_id, file_id),
    ).fetchone()
    return _file_version_from_row(row) if row is not None else None


def list_snapshot_files(conn: sqlite3.Connection, snapshot_id: int) -> list[SnapshotFileRecord]:
    """All files attached to ``snapshot_id``, ordered by path."""
    rows = conn.execute(
        """
        SELECT sf.snapshot_id, sf.file_id, sf.file_version_id
          FROM snapshot_files sf
          JOIN files f ON f.file_id = sf.file_id
         WHERE sf.snapshot_id = ?
         ORDER BY f.path
        """,
        (snapshot_id,),
    ).fetchall()
    return [_snapshot_file_from_row(row) for row in rows]


def list_snapshot_file_versions(
    conn: sqlite3.Connection, snapshot_id: int
) -> list[FileVersionRecord]:
    """All file versions attached to ``snapshot_id``, ordered by path."""
    rows = conn.execute(
        """
        SELECT fv.*
          FROM snapshot_files sf
          JOIN files f ON f.file_id = sf.file_id
          JOIN file_versions fv ON fv.file_version_id = sf.file_version_id
         WHERE sf.snapshot_id = ?
         ORDER BY f.path
        """,
        (snapshot_id,),
    ).fetchall()
    return [_file_version_from_row(row) for row in rows]


def get_file(conn: sqlite3.Connection, file_id: int) -> FileRecord | None:
    row = conn.execute("SELECT * FROM files WHERE file_id = ?", (file_id,)).fetchone()
    return _file_from_row(row) if row is not None else None


def get_file_version(conn: sqlite3.Connection, file_version_id: int) -> FileVersionRecord | None:
    row = conn.execute(
        "SELECT * FROM file_versions WHERE file_version_id = ?",
        (file_version_id,),
    ).fetchone()
    return _file_version_from_row(row) if row is not None else None


def replace_file_extraction(  # noqa: PLR0912
    conn: sqlite3.Connection,
    *,
    snapshot_id: int,
    worktree_id: int,
    extraction: FileExtractionReplacement,
    seen_at: str | None = None,
) -> FileVersionRecord:
    """Atomically replace one file version's extraction and attach it.

    Resolves the logical file, gets-or-creates the content-addressed file
    version, then clears and repopulates only that version's own child rows
    (imports, references, relationships, resource links, semantic unit
    versions) before attaching the version to ``snapshot_id``. Sibling file
    versions — including other versions of the same logical file — are
    never touched. The whole operation is one transaction: any failure
    rolls back everything, leaving prior state intact.

    Semantic units are processed in two passes so that a unit's
    ``parent_logical_key`` may reference a unit appearing later in
    ``extraction.units``: the first pass creates every unit and its version
    row with no parent, and the second pass resolves and fills in
    ``parent_unit_id`` now that every logical key in this extraction has a
    known ``unit_id``.
    """
    now = seen_at if seen_at is not None else _now()

    with transaction(conn):
        file_record = get_or_create_file(
            conn,
            worktree_id=worktree_id,
            relative_path=extraction.relative_path,
            seen_at=now,
        )
        file_version = get_or_create_file_version(
            conn,
            file_id=file_record.file_id,
            source_hash=extraction.source_hash,
            byte_count=extraction.byte_count,
            line_count=extraction.line_count,
            parse_status=extraction.parse_status,
            extractor_version=extraction.extractor_version,
            language=extraction.language,
            parse_error=extraction.parse_error,
            indexed_at=now,
        )
        file_version_id = file_version.file_version_id

        clear_file_version_graph_rows(conn, file_version_id)
        delete_extraction_for_file_version(conn, file_version_id)

        unit_id_by_logical_key: dict[str, int] = {}
        unit_version_id_by_logical_key: dict[str, int] = {}

        for unit_input in extraction.units:
            unit_record = get_or_create_semantic_unit(
                conn,
                file_id=file_record.file_id,
                logical_key=unit_input.logical_key,
                seen_at=now,
            )
            version_record = upsert_semantic_unit_version(
                conn,
                unit_id=unit_record.unit_id,
                file_version_id=file_version_id,
                language_kind=unit_input.language_kind,
                semantic_role=unit_input.semantic_role,
                qualified_name=unit_input.qualified_name,
                unqualified_name=unit_input.unqualified_name,
                signature=unit_input.signature,
                start_line=unit_input.start_line,
                end_line=unit_input.end_line,
                parent_unit_id=None,
                exported=unit_input.exported,
                metadata=unit_input.metadata,
            )
            unit_id_by_logical_key[unit_input.logical_key] = unit_record.unit_id
            unit_version_id_by_logical_key[unit_input.logical_key] = version_record.unit_version_id

        for unit_input in extraction.units:
            if unit_input.parent_logical_key is None:
                continue
            parent_unit_id = unit_id_by_logical_key.get(unit_input.parent_logical_key)
            if parent_unit_id is None:
                raise ValueError(
                    f"unit {unit_input.logical_key!r} references unknown parent "
                    f"logical key {unit_input.parent_logical_key!r}"
                )
            upsert_semantic_unit_version(
                conn,
                unit_id=unit_id_by_logical_key[unit_input.logical_key],
                file_version_id=file_version_id,
                language_kind=unit_input.language_kind,
                semantic_role=unit_input.semantic_role,
                qualified_name=unit_input.qualified_name,
                unqualified_name=unit_input.unqualified_name,
                signature=unit_input.signature,
                start_line=unit_input.start_line,
                end_line=unit_input.end_line,
                parent_unit_id=parent_unit_id,
                exported=unit_input.exported,
                metadata=unit_input.metadata,
            )

        def resolve_source_unit_version(logical_key: str | None) -> int | None:
            if logical_key is None:
                return None
            unit_version_id = unit_version_id_by_logical_key.get(logical_key)
            if unit_version_id is None:
                raise ValueError(
                    f"source_unit_logical_key {logical_key!r} was not found among "
                    f"this file version's units"
                )
            return unit_version_id

        for import_input in extraction.imports:
            insert_import(
                conn,
                file_version_id=file_version_id,
                source_unit_version_id=resolve_source_unit_version(
                    import_input.source_unit_logical_key
                ),
                module_specifier=import_input.module_specifier,
                imported_name=import_input.imported_name,
                local_alias=import_input.local_alias,
                import_kind=import_input.import_kind,
                start_line=import_input.start_line,
                end_line=import_input.end_line,
                metadata=import_input.metadata,
            )

        for reference_input in extraction.references:
            reference_record = insert_reference(
                conn,
                file_version_id=file_version_id,
                source_unit_version_id=resolve_source_unit_version(
                    reference_input.source_unit_logical_key
                ),
                identifier=reference_input.identifier,
                reference_kind=reference_input.reference_kind,
                start_line=reference_input.start_line,
                end_line=reference_input.end_line,
                resolution_method=reference_input.resolution_method,
                ambiguity_count=reference_input.ambiguity_count,
                metadata=reference_input.metadata,
            )
            for target_input in reference_input.targets:
                insert_reference_target(
                    conn,
                    reference_id=reference_record.reference_id,
                    target_unit_id=target_input.target_unit_id,
                    confidence=target_input.confidence,
                    is_preferred=target_input.is_preferred,
                    resolution_method=target_input.resolution_method,
                )

        for relationship_input in extraction.relationships:
            target_unit_id = relationship_input.target_unit_id
            target_file_id = relationship_input.target_file_id
            # Within-file targets are often carried as metadata logical keys until
            # unit rows exist; resolve them now that unit_id_by_logical_key is full.
            if target_unit_id is None and relationship_input.metadata:
                key = relationship_input.metadata.get("target_logical_key")
                if isinstance(key, str):
                    target_unit_id = unit_id_by_logical_key.get(key)
            if target_unit_id is None and target_file_id is None:
                # Cross-file / unresolved edges stay in metadata for a later
                # resolver pass; persistence requires a concrete target.
                continue
            insert_relationship(
                conn,
                source_file_version_id=file_version_id,
                source_unit_version_id=resolve_source_unit_version(
                    relationship_input.source_unit_logical_key
                ),
                relation_kind=relationship_input.relation_kind,
                confidence=relationship_input.confidence,
                resolution_method=relationship_input.resolution_method,
                target_file_id=target_file_id,
                target_unit_id=target_unit_id,
                start_line=relationship_input.start_line,
                end_line=relationship_input.end_line,
                metadata=relationship_input.metadata,
            )

        for resource_link_input in extraction.resource_links:
            source_unit_version_id = resolve_source_unit_version(
                resource_link_input.source_unit_logical_key
            )
            if source_unit_version_id is None:
                raise ValueError(
                    f"resource link source_unit_logical_key "
                    f"{resource_link_input.source_unit_logical_key!r} did not resolve "
                    f"to a unit in this extraction"
                )
            insert_resource_link(
                conn,
                source_unit_version_id=source_unit_version_id,
                target_file_id=resource_link_input.target_file_id,
                unresolved_path=resource_link_input.unresolved_path,
                resource_kind=resource_link_input.resource_kind,
                start_line=resource_link_input.start_line,
                end_line=resource_link_input.end_line,
                metadata=resource_link_input.metadata,
            )

        attach_file_to_snapshot(
            conn,
            snapshot_id=snapshot_id,
            file_id=file_record.file_id,
            file_version_id=file_version_id,
        )

    return file_version


__all__ = [
    "attach_file_to_snapshot",
    "get_file",
    "get_file_version",
    "get_or_create_file",
    "get_or_create_file_version",
    "get_snapshot_file_version",
    "list_snapshot_file_versions",
    "list_snapshot_files",
    "normalize_relative_path",
    "replace_file_extraction",
]
