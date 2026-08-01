"""Incremental indexing for the context compiler.

Public surface: worktree indexing, file enumeration, repository resolution,
current-snapshot queries, and the extraction→persistence mapper.
"""

from __future__ import annotations

from murder.context_compiler.indexing.coordinator import (
    TEXT_ONLY_EXTRACTOR_VERSION,
    UNSUPPORTED_EXTRACTOR_VERSION,
    index_worktree,
    index_worktree_sync,
)
from murder.context_compiler.indexing.files import (
    DEFAULT_HARD_READ_CEILING,
    DEFAULT_LEXICAL_SEARCH_CEILING,
    DEFAULT_STRUCTURAL_PARSE_CEILING,
    EnumeratedFile,
    FileClass,
    SizePolicy,
    classify_path,
    enumerate_worktree_files,
)
from murder.context_compiler.indexing.mapper import (
    build_local_id_to_logical_key,
    logical_key_for_unit,
    map_file_extraction,
)
from murder.context_compiler.indexing.queries import (
    FileHashComparison,
    SnapshotFileEntry,
    compare_current_and_previous_hashes,
    compare_file_hashes,
    find_unit_containing_line,
    get_file_version_by_path,
    get_ready_snapshots,
    list_current_files,
    list_imports_for_path,
    list_incoming_relationships,
    list_outgoing_relationships,
    list_references_for_path,
    list_resource_links_for_path,
    list_semantic_units_by_path,
    list_targets_for_reference,
    resolve_current_unit_version,
    search_units_by_name,
    search_units_by_semantic_role,
)
from murder.context_compiler.indexing.resolver import resolve_snapshot
from murder.context_compiler.indexing.state import (
    IndexDiagnosticSummary,
    IndexResult,
    ResolutionSummary,
)

__all__ = [
    "DEFAULT_HARD_READ_CEILING",
    "DEFAULT_LEXICAL_SEARCH_CEILING",
    "DEFAULT_STRUCTURAL_PARSE_CEILING",
    "EnumeratedFile",
    "FileClass",
    "FileHashComparison",
    "IndexDiagnosticSummary",
    "IndexResult",
    "ResolutionSummary",
    "SizePolicy",
    "SnapshotFileEntry",
    "TEXT_ONLY_EXTRACTOR_VERSION",
    "UNSUPPORTED_EXTRACTOR_VERSION",
    "build_local_id_to_logical_key",
    "classify_path",
    "compare_current_and_previous_hashes",
    "compare_file_hashes",
    "enumerate_worktree_files",
    "find_unit_containing_line",
    "get_file_version_by_path",
    "get_ready_snapshots",
    "index_worktree",
    "index_worktree_sync",
    "list_current_files",
    "list_imports_for_path",
    "list_incoming_relationships",
    "list_outgoing_relationships",
    "list_references_for_path",
    "list_resource_links_for_path",
    "list_semantic_units_by_path",
    "list_targets_for_reference",
    "logical_key_for_unit",
    "map_file_extraction",
    "resolve_current_unit_version",
    "resolve_snapshot",
    "search_units_by_name",
    "search_units_by_semantic_role",
]
