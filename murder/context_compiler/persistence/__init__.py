"""Experimental context-index persistence (``.murder/context-index.db``).

Separate from Murder's primary ``murder.db``. This package owns the schema,
connection lifecycle, snapshot/file/unit/relationship repositories, and the
two-ready-snapshot retention + garbage-collection policy used by a later
incremental structural indexer.

Typical indexer flow::

    conn = open_context_index(repo_root)
    worktree = get_or_create_worktree(...)
    snapshot = create_building_snapshot(...)
    for extraction in files:
        replace_file_extraction(conn, snapshot_id=..., worktree_id=..., extraction=...)
    mark_snapshot_ready(conn, snapshot.snapshot_id)
    apply_retention(conn, worktree.worktree_id)

Source bodies are never stored here; final exact evidence is read from the
live worktree by the Step 0 exact-evidence kernel.
"""

from __future__ import annotations

from murder.context_compiler.persistence.connection import (
    init_context_index_schema,
    open_context_index,
    transaction,
)
from murder.context_compiler.persistence.evidence_ledger import SqliteEvidenceLedger
from murder.context_compiler.persistence.files import (
    attach_file_to_snapshot,
    get_file,
    get_file_version,
    get_or_create_file,
    get_or_create_file_version,
    get_snapshot_file_version,
    list_snapshot_file_versions,
    list_snapshot_files,
    normalize_relative_path,
    replace_file_extraction,
)
from murder.context_compiler.persistence.records import (
    ConfidenceTier,
    CurrentPreviousSnapshots,
    FileExtractionReplacement,
    FileRecord,
    FileVersionRecord,
    ImportInput,
    ImportRecord,
    ParseStatus,
    ReferenceInput,
    ReferenceRecord,
    ReferenceTargetInput,
    ReferenceTargetRecord,
    RelationshipInput,
    RelationshipRecord,
    ResourceLinkInput,
    ResourceLinkRecord,
    RetentionResult,
    SemanticUnitRecord,
    SemanticUnitVersionInput,
    SemanticUnitVersionRecord,
    SnapshotFileRecord,
    SnapshotRecord,
    SnapshotStatus,
    WorktreeRecord,
)
from murder.context_compiler.persistence.relationships import (
    clear_file_version_graph_rows,
    clear_resolved_rows_for_snapshot,
    insert_import,
    insert_reference,
    insert_reference_target,
    insert_relationship,
    insert_resolved_reference_target,
    insert_resolved_relationship,
    insert_resource_link,
    list_imports_for_file_version,
    list_reference_targets,
    list_references_for_file_version,
    list_relationships_for_file_version,
    list_resolved_reference_targets,
    list_resolved_relationships_for_snapshot,
    list_resource_links_for_file_version,
)
from murder.context_compiler.persistence.retention import (
    apply_retention,
    cleanup_non_ready_snapshots,
    garbage_collect,
)
from murder.context_compiler.persistence.schema import SCHEMA_VERSION
from murder.context_compiler.persistence.semantic_units import (
    delete_extraction_for_file_version,
    dump_metadata,
    get_or_create_semantic_unit,
    get_semantic_unit,
    get_semantic_unit_version,
    list_child_unit_versions_in_snapshot,
    list_semantic_unit_versions_for_file_version,
    resolve_unit_version_in_snapshot,
    upsert_semantic_unit_version,
)
from murder.context_compiler.persistence.snapshots import (
    create_building_snapshot,
    delete_snapshot,
    get_current_and_previous_ready,
    get_newest_ready_snapshot,
    get_or_create_worktree,
    get_previous_ready_snapshot,
    get_snapshot,
    list_ready_snapshots,
    mark_snapshot_failed,
    mark_snapshot_ready,
)

__all__ = [
    "SCHEMA_VERSION",
    "ConfidenceTier",
    "CurrentPreviousSnapshots",
    "FileExtractionReplacement",
    "FileRecord",
    "FileVersionRecord",
    "ImportInput",
    "ImportRecord",
    "ParseStatus",
    "ReferenceInput",
    "ReferenceRecord",
    "ReferenceTargetInput",
    "ReferenceTargetRecord",
    "RelationshipInput",
    "RelationshipRecord",
    "ResourceLinkInput",
    "ResourceLinkRecord",
    "RetentionResult",
    "SemanticUnitRecord",
    "SemanticUnitVersionInput",
    "SemanticUnitVersionRecord",
    "SnapshotFileRecord",
    "SnapshotRecord",
    "SnapshotStatus",
    "SqliteEvidenceLedger",
    "WorktreeRecord",
    "apply_retention",
    "attach_file_to_snapshot",
    "cleanup_non_ready_snapshots",
    "clear_file_version_graph_rows",
    "clear_resolved_rows_for_snapshot",
    "create_building_snapshot",
    "delete_extraction_for_file_version",
    "delete_snapshot",
    "dump_metadata",
    "garbage_collect",
    "get_current_and_previous_ready",
    "get_file",
    "get_file_version",
    "get_newest_ready_snapshot",
    "get_or_create_file",
    "get_or_create_file_version",
    "get_or_create_semantic_unit",
    "get_or_create_worktree",
    "get_previous_ready_snapshot",
    "get_semantic_unit",
    "get_semantic_unit_version",
    "get_snapshot",
    "get_snapshot_file_version",
    "init_context_index_schema",
    "insert_import",
    "insert_reference",
    "insert_reference_target",
    "insert_relationship",
    "insert_resolved_reference_target",
    "insert_resolved_relationship",
    "insert_resource_link",
    "list_child_unit_versions_in_snapshot",
    "list_imports_for_file_version",
    "list_ready_snapshots",
    "list_reference_targets",
    "list_references_for_file_version",
    "list_relationships_for_file_version",
    "list_resolved_reference_targets",
    "list_resolved_relationships_for_snapshot",
    "list_resource_links_for_file_version",
    "list_semantic_unit_versions_for_file_version",
    "list_snapshot_file_versions",
    "list_snapshot_files",
    "mark_snapshot_failed",
    "mark_snapshot_ready",
    "normalize_relative_path",
    "open_context_index",
    "replace_file_extraction",
    "resolve_unit_version_in_snapshot",
    "transaction",
    "upsert_semantic_unit_version",
]
