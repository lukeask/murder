"""Persistence for imports, references, relationships, and resource links.

These tables record the *edges* of the context graph, layered on top of the
versioned files / semantic units in :mod:`murder.context_compiler.persistence.
semantic_units`:

* ``imports`` and ``"references"`` (the latter is a SQL keyword and must
  always be quoted in statements) are raw extracted facts — the imports a
  file declares and the identifier references it makes — kept as-is rather
  than pre-resolved.
* ``reference_targets`` records local (same-file) resolution candidates
  attached at extraction time.
* ``resolved_reference_targets`` records cross-file resolutions for an
  explicit ``snapshot_id``. Reused file versions do not reuse these rows.
* ``relationships`` holds local extraction edges (contains, same-file calls).
* ``resolved_relationships`` holds cross-file edges written by the resolver,
  always keyed by ``snapshot_id``.
* ``resource_links`` records non-code resource references (e.g. config
  files, templates, assets) made from a semantic unit.

Across all edge tables, *sources* always point at an exact version — the
``file_versions`` row (and optionally the ``semantic_unit_versions`` row
within it) where the evidence was observed. *Targets* normally point at
logical identities (``files`` / ``semantic_units``) rather than versions, so
an edge survives ordinary edits and line movement in the target instead of
going stale the moment the target file changes.

Confidence is always one of ``exact`` / ``inferred`` / ``weak``.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from murder.context_compiler.indexing.resolution_policy import (
    normalize_confidence,
    tier_rank,
)
from murder.context_compiler.persistence.records import (
    ConfidenceTier,
    ImportRecord,
    ReferenceRecord,
    ReferenceTargetRecord,
    RelationshipRecord,
    ResourceLinkRecord,
)
from murder.context_compiler.persistence.semantic_units import dump_metadata


def _validate_line_range(start_line: int, end_line: int) -> None:
    if start_line < 1:
        raise ValueError(f"start_line must be >= 1, got {start_line}")
    if end_line < start_line:
        raise ValueError(f"end_line ({end_line}) must be >= start_line ({start_line})")


def _validate_optional_line_range(start_line: int | None, end_line: int | None) -> None:
    if start_line is None and end_line is None:
        return
    if start_line is None or end_line is None:
        raise ValueError("start_line and end_line must both be set or both be None")
    _validate_line_range(start_line, end_line)


def insert_import(
    conn: sqlite3.Connection,
    *,
    file_version_id: int,
    module_specifier: str,
    import_kind: str,
    start_line: int,
    end_line: int,
    imported_name: str | None = None,
    local_alias: str | None = None,
    source_unit_version_id: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> ImportRecord:
    """Insert one raw import fact for a file version."""
    _validate_line_range(start_line, end_line)
    metadata_json = dump_metadata(metadata)
    cursor = conn.execute(
        """
        INSERT INTO imports (
            file_version_id, source_unit_version_id, module_specifier,
            imported_name, local_alias, import_kind, start_line, end_line,
            metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            file_version_id,
            source_unit_version_id,
            module_specifier,
            imported_name,
            local_alias,
            import_kind,
            start_line,
            end_line,
            metadata_json,
        ),
    )
    import_id = cursor.lastrowid
    assert import_id is not None
    return ImportRecord(
        import_id=import_id,
        file_version_id=file_version_id,
        source_unit_version_id=source_unit_version_id,
        module_specifier=module_specifier,
        imported_name=imported_name,
        local_alias=local_alias,
        import_kind=import_kind,
        start_line=start_line,
        end_line=end_line,
        metadata_json=metadata_json,
    )


def insert_reference(
    conn: sqlite3.Connection,
    *,
    file_version_id: int,
    identifier: str,
    reference_kind: str,
    start_line: int,
    end_line: int,
    source_unit_version_id: int | None = None,
    resolution_method: str | None = None,
    ambiguity_count: int = 0,
    metadata: dict[str, Any] | None = None,
) -> ReferenceRecord:
    """Insert one raw identifier-reference fact for a file version."""
    _validate_line_range(start_line, end_line)
    metadata_json = dump_metadata(metadata)
    cursor = conn.execute(
        """
        INSERT INTO "references" (
            file_version_id, source_unit_version_id, identifier, reference_kind,
            start_line, end_line, resolution_method, ambiguity_count, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            file_version_id,
            source_unit_version_id,
            identifier,
            reference_kind,
            start_line,
            end_line,
            resolution_method,
            ambiguity_count,
            metadata_json,
        ),
    )
    reference_id = cursor.lastrowid
    assert reference_id is not None
    return ReferenceRecord(
        reference_id=reference_id,
        file_version_id=file_version_id,
        source_unit_version_id=source_unit_version_id,
        identifier=identifier,
        reference_kind=reference_kind,
        start_line=start_line,
        end_line=end_line,
        resolution_method=resolution_method,
        ambiguity_count=ambiguity_count,
        metadata_json=metadata_json,
    )


def insert_reference_target(
    conn: sqlite3.Connection,
    *,
    reference_id: int,
    target_unit_id: int,
    confidence: ConfidenceTier | float,
    resolution_method: str,
    is_preferred: bool = False,
) -> ReferenceTargetRecord:
    """Attach one local (same-file) resolution candidate to a reference."""
    tier = normalize_confidence(confidence)
    conn.execute(
        """
        INSERT INTO reference_targets (
            reference_id, target_unit_id, confidence, is_preferred, resolution_method
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (reference_id, target_unit_id, tier, 1 if is_preferred else 0, resolution_method),
    )
    return ReferenceTargetRecord(
        reference_id=reference_id,
        target_unit_id=target_unit_id,
        confidence=tier,
        is_preferred=is_preferred,
        resolution_method=resolution_method,
        snapshot_id=None,
    )


def insert_resolved_reference_target(
    conn: sqlite3.Connection,
    *,
    snapshot_id: int,
    reference_id: int,
    target_unit_id: int,
    confidence: ConfidenceTier | float,
    resolution_method: str,
    is_preferred: bool = False,
) -> ReferenceTargetRecord:
    """Attach one snapshot-scoped cross-file resolution candidate."""
    tier = normalize_confidence(confidence)
    conn.execute(
        """
        INSERT INTO resolved_reference_targets (
            snapshot_id, reference_id, target_unit_id, confidence,
            is_preferred, resolution_method
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            snapshot_id,
            reference_id,
            target_unit_id,
            tier,
            1 if is_preferred else 0,
            resolution_method,
        ),
    )
    return ReferenceTargetRecord(
        reference_id=reference_id,
        target_unit_id=target_unit_id,
        confidence=tier,
        is_preferred=is_preferred,
        resolution_method=resolution_method,
        snapshot_id=snapshot_id,
    )


def insert_relationship(
    conn: sqlite3.Connection,
    *,
    source_file_version_id: int,
    relation_kind: str,
    confidence: ConfidenceTier | float,
    resolution_method: str,
    source_unit_version_id: int | None = None,
    target_file_id: int | None = None,
    target_unit_id: int | None = None,
    start_line: int | None = None,
    end_line: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> RelationshipRecord:
    """Insert one local extraction relationship edge.

    Raises ``ValueError`` if neither ``target_file_id`` nor ``target_unit_id``
    is given (an edge must point somewhere), or if only one of ``start_line`` /
    ``end_line`` is given.
    """
    if target_file_id is None and target_unit_id is None:
        raise ValueError("insert_relationship requires target_file_id or target_unit_id")
    _validate_optional_line_range(start_line, end_line)
    tier = normalize_confidence(confidence)
    metadata_json = dump_metadata(metadata)
    cursor = conn.execute(
        """
        INSERT INTO relationships (
            source_file_version_id, source_unit_version_id, target_file_id,
            target_unit_id, relation_kind, start_line, end_line, confidence,
            resolution_method, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source_file_version_id,
            source_unit_version_id,
            target_file_id,
            target_unit_id,
            relation_kind,
            start_line,
            end_line,
            tier,
            resolution_method,
            metadata_json,
        ),
    )
    relationship_id = cursor.lastrowid
    assert relationship_id is not None
    return RelationshipRecord(
        relationship_id=relationship_id,
        source_file_version_id=source_file_version_id,
        source_unit_version_id=source_unit_version_id,
        target_file_id=target_file_id,
        target_unit_id=target_unit_id,
        relation_kind=relation_kind,
        start_line=start_line,
        end_line=end_line,
        confidence=tier,
        resolution_method=resolution_method,
        metadata_json=metadata_json,
        snapshot_id=None,
    )


def insert_resolved_relationship(
    conn: sqlite3.Connection,
    *,
    snapshot_id: int,
    source_file_version_id: int,
    relation_kind: str,
    confidence: ConfidenceTier | float,
    resolution_method: str,
    source_unit_version_id: int | None = None,
    target_file_id: int | None = None,
    target_unit_id: int | None = None,
    start_line: int | None = None,
    end_line: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> RelationshipRecord:
    """Insert one snapshot-scoped cross-file relationship edge."""
    if target_file_id is None and target_unit_id is None:
        raise ValueError("insert_resolved_relationship requires target_file_id or target_unit_id")
    _validate_optional_line_range(start_line, end_line)
    tier = normalize_confidence(confidence)
    metadata_json = dump_metadata(metadata)
    cursor = conn.execute(
        """
        INSERT INTO resolved_relationships (
            snapshot_id, source_file_version_id, source_unit_version_id,
            target_file_id, target_unit_id, relation_kind, start_line, end_line,
            confidence, resolution_method, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            snapshot_id,
            source_file_version_id,
            source_unit_version_id,
            target_file_id,
            target_unit_id,
            relation_kind,
            start_line,
            end_line,
            tier,
            resolution_method,
            metadata_json,
        ),
    )
    relationship_id = cursor.lastrowid
    assert relationship_id is not None
    return RelationshipRecord(
        relationship_id=relationship_id,
        source_file_version_id=source_file_version_id,
        source_unit_version_id=source_unit_version_id,
        target_file_id=target_file_id,
        target_unit_id=target_unit_id,
        relation_kind=relation_kind,
        start_line=start_line,
        end_line=end_line,
        confidence=tier,
        resolution_method=resolution_method,
        metadata_json=metadata_json,
        snapshot_id=snapshot_id,
    )


def insert_resource_link(
    conn: sqlite3.Connection,
    *,
    source_unit_version_id: int,
    resource_kind: str,
    target_file_id: int | None = None,
    unresolved_path: str | None = None,
    start_line: int | None = None,
    end_line: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> ResourceLinkRecord:
    """Insert one non-code resource reference made from a semantic unit.

    Raises ``ValueError`` if neither ``target_file_id`` nor
    ``unresolved_path`` is given, or if only one of ``start_line`` /
    ``end_line`` is given.
    """
    if target_file_id is None and unresolved_path is None:
        raise ValueError("insert_resource_link requires target_file_id or unresolved_path")
    _validate_optional_line_range(start_line, end_line)
    metadata_json = dump_metadata(metadata)
    cursor = conn.execute(
        """
        INSERT INTO resource_links (
            source_unit_version_id, target_file_id, unresolved_path, resource_kind,
            start_line, end_line, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source_unit_version_id,
            target_file_id,
            unresolved_path,
            resource_kind,
            start_line,
            end_line,
            metadata_json,
        ),
    )
    resource_link_id = cursor.lastrowid
    assert resource_link_id is not None
    return ResourceLinkRecord(
        resource_link_id=resource_link_id,
        source_unit_version_id=source_unit_version_id,
        target_file_id=target_file_id,
        unresolved_path=unresolved_path,
        resource_kind=resource_kind,
        start_line=start_line,
        end_line=end_line,
        metadata_json=metadata_json,
    )


def clear_resolved_rows_for_snapshot(conn: sqlite3.Connection, snapshot_id: int) -> None:
    """Delete all resolver-written rows for ``snapshot_id``.

    Does not touch extraction facts (imports, references, local relationships,
    resource links, units). Composable with an ambient transaction.
    """
    conn.execute(
        "DELETE FROM resolved_reference_targets WHERE snapshot_id = ?",
        (snapshot_id,),
    )
    conn.execute(
        "DELETE FROM resolved_relationships WHERE snapshot_id = ?",
        (snapshot_id,),
    )


def clear_file_version_graph_rows(conn: sqlite3.Connection, file_version_id: int) -> None:
    """Delete this file version's imports, references, local relationships, and
    resource links, in an order that never trips a foreign-key check.

    ``reference_targets`` rows cascade when their owning ``"references"`` row
    is deleted. ``resolved_*`` rows that reference this file version cascade
    via FK or are cleared by snapshot replace — not touched here.

    Composable with an ambient transaction: if the connection is already
    inside one (e.g. a caller doing a full extraction replace that will also
    delete this file version's ``semantic_unit_versions``), the deletes join
    that transaction instead of starting a nested one.
    """
    owns_transaction = conn.isolation_level is None and not conn.in_transaction
    if owns_transaction:
        conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            """
            DELETE FROM resource_links
             WHERE source_unit_version_id IN (
                 SELECT unit_version_id FROM semantic_unit_versions
                  WHERE file_version_id = ?
             )
            """,
            (file_version_id,),
        )
        conn.execute(
            "DELETE FROM relationships WHERE source_file_version_id = ?",
            (file_version_id,),
        )
        conn.execute("DELETE FROM imports WHERE file_version_id = ?", (file_version_id,))
        conn.execute('DELETE FROM "references" WHERE file_version_id = ?', (file_version_id,))
    except BaseException:
        if owns_transaction:
            conn.rollback()
        raise
    else:
        if owns_transaction:
            conn.commit()


def list_imports_for_file_version(
    conn: sqlite3.Connection, file_version_id: int
) -> list[ImportRecord]:
    """List imports declared in a file version, in source order."""
    rows = conn.execute(
        """
        SELECT import_id, file_version_id, source_unit_version_id, module_specifier,
               imported_name, local_alias, import_kind, start_line, end_line,
               metadata_json
          FROM imports
         WHERE file_version_id = ?
         ORDER BY start_line, import_id
        """,
        (file_version_id,),
    ).fetchall()
    return [_row_to_import(row) for row in rows]


def list_references_for_file_version(
    conn: sqlite3.Connection, file_version_id: int
) -> list[ReferenceRecord]:
    """List identifier references made in a file version, in source order."""
    rows = conn.execute(
        """
        SELECT reference_id, file_version_id, source_unit_version_id, identifier,
               reference_kind, start_line, end_line, resolution_method,
               ambiguity_count, metadata_json
          FROM "references"
         WHERE file_version_id = ?
         ORDER BY start_line, reference_id
        """,
        (file_version_id,),
    ).fetchall()
    return [_row_to_reference(row) for row in rows]


def list_reference_targets(
    conn: sqlite3.Connection, reference_id: int
) -> list[ReferenceTargetRecord]:
    """List local (extraction-time) resolution candidates for a reference."""
    rows = conn.execute(
        """
        SELECT reference_id, target_unit_id, confidence, is_preferred, resolution_method
          FROM reference_targets
         WHERE reference_id = ?
         ORDER BY is_preferred DESC, confidence, target_unit_id
        """,
        (reference_id,),
    ).fetchall()
    return sorted(
        [_row_to_reference_target(row, snapshot_id=None) for row in rows],
        key=lambda t: (-int(t.is_preferred), -tier_rank(t.confidence), t.target_unit_id),
    )


def list_resolved_reference_targets(
    conn: sqlite3.Connection,
    *,
    snapshot_id: int,
    reference_id: int,
) -> list[ReferenceTargetRecord]:
    """List snapshot-scoped cross-file targets for a reference.

    Requires an explicit ``snapshot_id`` — there is no "latest" fallback.
    """
    rows = conn.execute(
        """
        SELECT snapshot_id, reference_id, target_unit_id, confidence,
               is_preferred, resolution_method
          FROM resolved_reference_targets
         WHERE snapshot_id = ? AND reference_id = ?
         ORDER BY is_preferred DESC, confidence, target_unit_id
        """,
        (snapshot_id, reference_id),
    ).fetchall()
    return sorted(
        [_row_to_reference_target(row, snapshot_id=int(row["snapshot_id"])) for row in rows],
        key=lambda t: (-int(t.is_preferred), -tier_rank(t.confidence), t.target_unit_id),
    )


def list_relationships_for_file_version(
    conn: sqlite3.Connection, file_version_id: int
) -> list[RelationshipRecord]:
    """List local extraction relationship edges sourced from a file version."""
    rows = conn.execute(
        """
        SELECT relationship_id, source_file_version_id, source_unit_version_id,
               target_file_id, target_unit_id, relation_kind, start_line, end_line,
               confidence, resolution_method, metadata_json
          FROM relationships
         WHERE source_file_version_id = ?
         ORDER BY relationship_id
        """,
        (file_version_id,),
    ).fetchall()
    return [_row_to_relationship(row, snapshot_id=None) for row in rows]


def list_resolved_relationships_for_snapshot(
    conn: sqlite3.Connection,
    *,
    snapshot_id: int,
    source_file_version_id: int | None = None,
) -> list[RelationshipRecord]:
    """List resolver-written relationships for ``snapshot_id``.

    When ``source_file_version_id`` is set, filter to that source version.
    """
    if source_file_version_id is None:
        rows = conn.execute(
            """
            SELECT relationship_id, snapshot_id, source_file_version_id,
                   source_unit_version_id, target_file_id, target_unit_id,
                   relation_kind, start_line, end_line, confidence,
                   resolution_method, metadata_json
              FROM resolved_relationships
             WHERE snapshot_id = ?
             ORDER BY relationship_id
            """,
            (snapshot_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT relationship_id, snapshot_id, source_file_version_id,
                   source_unit_version_id, target_file_id, target_unit_id,
                   relation_kind, start_line, end_line, confidence,
                   resolution_method, metadata_json
              FROM resolved_relationships
             WHERE snapshot_id = ? AND source_file_version_id = ?
             ORDER BY relationship_id
            """,
            (snapshot_id, source_file_version_id),
        ).fetchall()
    return [_row_to_relationship(row, snapshot_id=int(row["snapshot_id"])) for row in rows]


def list_resource_links_for_file_version(
    conn: sqlite3.Connection, file_version_id: int
) -> list[ResourceLinkRecord]:
    """List resource links sourced from any semantic unit in a file version."""
    rows = conn.execute(
        """
        SELECT rl.resource_link_id, rl.source_unit_version_id, rl.target_file_id,
               rl.unresolved_path, rl.resource_kind, rl.start_line, rl.end_line,
               rl.metadata_json
          FROM resource_links AS rl
          JOIN semantic_unit_versions AS suv
            ON suv.unit_version_id = rl.source_unit_version_id
         WHERE suv.file_version_id = ?
         ORDER BY rl.resource_link_id
        """,
        (file_version_id,),
    ).fetchall()
    return [_row_to_resource_link(row) for row in rows]


def _row_to_import(row: sqlite3.Row) -> ImportRecord:
    return ImportRecord(
        import_id=int(row["import_id"]),
        file_version_id=int(row["file_version_id"]),
        source_unit_version_id=row["source_unit_version_id"],
        module_specifier=str(row["module_specifier"]),
        imported_name=row["imported_name"],
        local_alias=row["local_alias"],
        import_kind=str(row["import_kind"]),
        start_line=int(row["start_line"]),
        end_line=int(row["end_line"]),
        metadata_json=str(row["metadata_json"]),
    )


def _row_to_reference(row: sqlite3.Row) -> ReferenceRecord:
    return ReferenceRecord(
        reference_id=int(row["reference_id"]),
        file_version_id=int(row["file_version_id"]),
        source_unit_version_id=row["source_unit_version_id"],
        identifier=str(row["identifier"]),
        reference_kind=str(row["reference_kind"]),
        start_line=int(row["start_line"]),
        end_line=int(row["end_line"]),
        resolution_method=row["resolution_method"],
        ambiguity_count=int(row["ambiguity_count"]),
        metadata_json=str(row["metadata_json"]),
    )


def _row_to_reference_target(row: sqlite3.Row, *, snapshot_id: int | None) -> ReferenceTargetRecord:
    return ReferenceTargetRecord(
        reference_id=int(row["reference_id"]),
        target_unit_id=int(row["target_unit_id"]),
        confidence=normalize_confidence(str(row["confidence"])),
        is_preferred=bool(row["is_preferred"]),
        resolution_method=str(row["resolution_method"]),
        snapshot_id=snapshot_id,
    )


def _row_to_relationship(row: sqlite3.Row, *, snapshot_id: int | None) -> RelationshipRecord:
    return RelationshipRecord(
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
        snapshot_id=snapshot_id,
    )


def _row_to_resource_link(row: sqlite3.Row) -> ResourceLinkRecord:
    return ResourceLinkRecord(
        resource_link_id=int(row["resource_link_id"]),
        source_unit_version_id=int(row["source_unit_version_id"]),
        target_file_id=row["target_file_id"],
        unresolved_path=row["unresolved_path"],
        resource_kind=str(row["resource_kind"]),
        start_line=row["start_line"],
        end_line=row["end_line"],
        metadata_json=str(row["metadata_json"]),
    )


__all__ = [
    "clear_file_version_graph_rows",
    "clear_resolved_rows_for_snapshot",
    "insert_import",
    "insert_reference",
    "insert_reference_target",
    "insert_resolved_reference_target",
    "insert_relationship",
    "insert_resolved_relationship",
    "insert_resource_link",
    "list_imports_for_file_version",
    "list_reference_targets",
    "list_references_for_file_version",
    "list_relationships_for_file_version",
    "list_resolved_reference_targets",
    "list_resolved_relationships_for_snapshot",
    "list_resource_links_for_file_version",
]
