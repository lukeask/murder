"""Index-run result and related state types for the incremental coordinator."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class IndexDiagnosticSummary:
    """Aggregated diagnostic counts — not every parser message."""

    errors: int = 0
    warnings: int = 0
    infos: int = 0
    sample_messages: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ResolutionSummary:
    """Counts from the repository-level resolution pass."""

    imports_resolved_to_files: int = 0
    imported_names_resolved: int = 0
    reference_targets_written: int = 0
    relationships_added: int = 0
    resource_links_resolved: int = 0
    skipped_ambiguous: int = 0


@dataclass(frozen=True, slots=True)
class IndexResult:
    """Outcome of one ``index_worktree`` run."""

    snapshot_id: int
    worktree_id: int
    state_timestamp: str
    status: str
    files_discovered: int = 0
    files_reused: int = 0
    files_parsed: int = 0
    files_text_only: int = 0
    files_unsupported: int = 0
    files_failed: int = 0
    files_ignored: int = 0
    semantic_units_written: int = 0
    imports_written: int = 0
    references_written: int = 0
    relationships_written: int = 0
    resource_links_written: int = 0
    diagnostics: IndexDiagnosticSummary = field(default_factory=IndexDiagnosticSummary)
    resolution: ResolutionSummary = field(default_factory=ResolutionSummary)
    deleted_snapshots: int = 0
    deleted_file_versions: int = 0
    deleted_semantic_unit_versions: int = 0
    deleted_semantic_units: int = 0
    deleted_files: int = 0
    failure_reason: str | None = None


__all__ = [
    "IndexDiagnosticSummary",
    "IndexResult",
    "ResolutionSummary",
]
