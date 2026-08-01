"""Immutable row records for the context-index persistence layer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

SnapshotStatus = Literal["building", "ready", "failed"]
ParseStatus = Literal["parsed", "partial", "text_only", "unsupported", "failed"]
# Persisted resolution certainty. Precedence ranks map onto these tiers;
# see indexing.resolution_policy. Never a float probability.
ConfidenceTier = Literal["exact", "inferred", "weak"]


@dataclass(frozen=True, slots=True)
class WorktreeRecord:
    worktree_id: int
    repository_root: str
    worktree_root: str
    created_at: str
    last_seen_at: str


@dataclass(frozen=True, slots=True)
class SnapshotRecord:
    snapshot_id: int
    worktree_id: int
    state_timestamp: str
    commit_sha: str | None
    status: SnapshotStatus
    generated_at: str
    failure_reason: str | None


@dataclass(frozen=True, slots=True)
class CurrentPreviousSnapshots:
    """Newest and second-newest ready snapshots for a worktree.

    Ordering is ``state_timestamp DESC, snapshot_id DESC`` — never
    ``generated_at``.
    """

    current: SnapshotRecord | None
    previous: SnapshotRecord | None


@dataclass(frozen=True, slots=True)
class FileRecord:
    file_id: int
    worktree_id: int
    path: str
    first_seen_at: str
    last_seen_at: str


@dataclass(frozen=True, slots=True)
class FileVersionRecord:
    file_version_id: int
    file_id: int
    source_hash: str
    language: str | None
    byte_count: int
    line_count: int
    parse_status: ParseStatus
    parse_error: str | None
    extractor_version: str
    indexed_at: str


@dataclass(frozen=True, slots=True)
class SnapshotFileRecord:
    snapshot_id: int
    file_id: int
    file_version_id: int


@dataclass(frozen=True, slots=True)
class SemanticUnitRecord:
    unit_id: int
    file_id: int
    logical_key: str
    first_seen_at: str
    last_seen_at: str


@dataclass(frozen=True, slots=True)
class SemanticUnitVersionRecord:
    unit_version_id: int
    unit_id: int
    file_version_id: int
    language_kind: str
    semantic_role: str | None
    qualified_name: str
    unqualified_name: str
    signature: str | None
    start_line: int
    end_line: int
    parent_unit_id: int | None
    exported: bool
    metadata_json: str


@dataclass(frozen=True, slots=True)
class ImportRecord:
    import_id: int
    file_version_id: int
    source_unit_version_id: int | None
    module_specifier: str
    imported_name: str | None
    local_alias: str | None
    import_kind: str
    start_line: int
    end_line: int
    metadata_json: str


@dataclass(frozen=True, slots=True)
class ReferenceRecord:
    reference_id: int
    file_version_id: int
    source_unit_version_id: int | None
    identifier: str
    reference_kind: str
    start_line: int
    end_line: int
    resolution_method: str | None
    ambiguity_count: int
    metadata_json: str


@dataclass(frozen=True, slots=True)
class ReferenceTargetRecord:
    """One resolution candidate for a reference.

    ``snapshot_id`` is set for cross-file (resolved) targets and ``None`` for
    local extraction-time targets.
    """

    reference_id: int
    target_unit_id: int
    confidence: ConfidenceTier
    is_preferred: bool
    resolution_method: str
    snapshot_id: int | None = None


@dataclass(frozen=True, slots=True)
class RelationshipRecord:
    """One relationship edge.

    ``snapshot_id`` is set for resolver-written cross-file edges and ``None``
    for local extraction edges reusable with the file version.
    """

    relationship_id: int
    source_file_version_id: int
    source_unit_version_id: int | None
    target_file_id: int | None
    target_unit_id: int | None
    relation_kind: str
    start_line: int | None
    end_line: int | None
    confidence: ConfidenceTier
    resolution_method: str
    metadata_json: str
    snapshot_id: int | None = None


@dataclass(frozen=True, slots=True)
class ResourceLinkRecord:
    resource_link_id: int
    source_unit_version_id: int
    target_file_id: int | None
    unresolved_path: str | None
    resource_kind: str
    start_line: int | None
    end_line: int | None
    metadata_json: str


@dataclass(frozen=True, slots=True)
class RetentionResult:
    """Counts from ready-snapshot pruning and subsequent garbage collection."""

    deleted_snapshots: int
    deleted_file_versions: int
    deleted_semantic_unit_versions: int
    deleted_semantic_units: int
    deleted_files: int
    deleted_building_or_failed: int = 0


# --- input payloads for replace_file_extraction ---


@dataclass(frozen=True, slots=True)
class SemanticUnitVersionInput:
    """Logical key + version fields for one unit within a file version."""

    logical_key: str
    language_kind: str
    qualified_name: str
    unqualified_name: str
    start_line: int
    end_line: int
    semantic_role: str | None = None
    signature: str | None = None
    parent_logical_key: str | None = None
    exported: bool = False
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ImportInput:
    module_specifier: str
    import_kind: str
    start_line: int
    end_line: int
    imported_name: str | None = None
    local_alias: str | None = None
    source_unit_logical_key: str | None = None
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ReferenceTargetInput:
    target_unit_id: int
    confidence: ConfidenceTier
    resolution_method: str
    is_preferred: bool = False


@dataclass(frozen=True, slots=True)
class ReferenceInput:
    identifier: str
    reference_kind: str
    start_line: int
    end_line: int
    source_unit_logical_key: str | None = None
    resolution_method: str | None = None
    ambiguity_count: int = 0
    targets: tuple[ReferenceTargetInput, ...] = ()
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class RelationshipInput:
    relation_kind: str
    confidence: ConfidenceTier | float
    resolution_method: str
    source_unit_logical_key: str | None = None
    target_file_id: int | None = None
    target_unit_id: int | None = None
    start_line: int | None = None
    end_line: int | None = None
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ResourceLinkInput:
    resource_kind: str
    source_unit_logical_key: str
    target_file_id: int | None = None
    unresolved_path: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class FileExtractionReplacement:
    """All extracted records for one file version, replaced atomically."""

    relative_path: str
    source_hash: str
    byte_count: int
    line_count: int
    parse_status: ParseStatus
    extractor_version: str
    language: str | None = None
    parse_error: str | None = None
    units: tuple[SemanticUnitVersionInput, ...] = ()
    imports: tuple[ImportInput, ...] = ()
    references: tuple[ReferenceInput, ...] = ()
    relationships: tuple[RelationshipInput, ...] = ()
    resource_links: tuple[ResourceLinkInput, ...] = ()


__all__ = [
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
    "WorktreeRecord",
]
