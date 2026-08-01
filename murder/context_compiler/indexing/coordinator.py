"""Incremental worktree indexing coordinator.

Owns file enumeration, source hashing, extractor selection, file-version reuse,
persistence, snapshot lifecycle, repository-level resolution, and retention.

Does not register a background worker. Synchronous internals run off the async
event loop via ``asyncio.to_thread``.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from murder.context_compiler.extraction.common import EXTRACTION_SCHEMA_VERSION
from murder.context_compiler.extraction.models import FileExtraction
from murder.context_compiler.extraction.registry import (
    ExtractorRegistry,
    default_registry,
)
from murder.context_compiler.indexing.files import (
    EnumeratedFile,
    FileClass,
    SizePolicy,
    enumerate_worktree_files,
    looks_binary,
)
from murder.context_compiler.indexing.mapper import map_file_extraction
from murder.context_compiler.indexing.resolver import resolve_snapshot
from murder.context_compiler.indexing.state import (
    IndexDiagnosticSummary,
    IndexResult,
    ResolutionSummary,
)
from murder.context_compiler.persistence import (
    apply_retention,
    attach_file_to_snapshot,
    create_building_snapshot,
    get_or_create_file,
    get_or_create_worktree,
    mark_snapshot_failed,
    mark_snapshot_ready,
    open_context_index,
    replace_file_extraction,
    transaction,
)
from murder.context_compiler.persistence.records import (
    FileExtractionReplacement,
    FileVersionRecord,
    ParseStatus,
)
from murder.context_compiler.source import count_source_lines, hash_source_bytes

# Synthetic extractor versions for non-structural attachments.
TEXT_ONLY_EXTRACTOR_VERSION = f"{EXTRACTION_SCHEMA_VERSION}:text-only-1"
UNSUPPORTED_EXTRACTOR_VERSION = f"{EXTRACTION_SCHEMA_VERSION}:unsupported-1"


def _find_file_version(
    conn: sqlite3.Connection,
    *,
    file_id: int,
    source_hash: str,
    extractor_version: str,
) -> FileVersionRecord | None:
    """Lookup existing content-addressed version without creating a stub."""
    row = conn.execute(
        """
        SELECT file_version_id, file_id, source_hash, language, byte_count, line_count,
               parse_status, parse_error, extractor_version, indexed_at
          FROM file_versions
         WHERE file_id = ? AND source_hash = ? AND extractor_version = ?
        """,
        (file_id, source_hash, extractor_version),
    ).fetchone()
    if row is None:
        return None
    return FileVersionRecord(
        file_version_id=int(row["file_version_id"]),
        file_id=int(row["file_id"]),
        source_hash=str(row["source_hash"]),
        language=row["language"],
        byte_count=int(row["byte_count"]),
        line_count=int(row["line_count"]),
        parse_status=row["parse_status"],
        parse_error=row["parse_error"],
        extractor_version=str(row["extractor_version"]),
        indexed_at=str(row["indexed_at"]),
    )


def _read_source(worktree_root: Path, relative_path: str) -> tuple[bytes, str, int] | None:
    """Read bytes from disk; return ``(raw, text_or_empty, line_count)`` or None."""
    path = worktree_root / relative_path
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if looks_binary(raw):
        return None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = raw.decode("utf-8", errors="replace")
        except Exception:
            return None
    return raw, text, count_source_lines(text)


def _text_only_replacement(
    *,
    relative_path: str,
    source_hash: str,
    byte_count: int,
    line_count: int,
    language: str | None,
    parse_status: ParseStatus = "text_only",
    extractor_version: str = TEXT_ONLY_EXTRACTOR_VERSION,
    parse_error: str | None = None,
) -> FileExtractionReplacement:
    return FileExtractionReplacement(
        relative_path=relative_path,
        source_hash=source_hash,
        byte_count=byte_count,
        line_count=line_count,
        parse_status=parse_status,
        extractor_version=extractor_version,
        language=language,
        parse_error=parse_error,
    )


def _extract_structural(
    *,
    relative_path: str,
    text: str,
    source_hash: str,
    byte_count: int,
    line_count: int,
    registry: ExtractorRegistry,
) -> tuple[FileExtractionReplacement, IndexDiagnosticSummary, str]:
    """Run the selected pipeline and map to a persistence replacement.

    Returns ``(replacement, diagnostics, outcome)`` where outcome is one of
    ``parsed``, ``partial``, ``failed``, ``unsupported``.
    """
    pipeline = registry.select(relative_path, source=text)
    if pipeline is None:
        return (
            _text_only_replacement(
                relative_path=relative_path,
                source_hash=source_hash,
                byte_count=byte_count,
                line_count=line_count,
                language=registry.resolve_language(relative_path),
                parse_status="unsupported",
                extractor_version=UNSUPPORTED_EXTRACTOR_VERSION,
                parse_error="no extractor registered",
            ),
            IndexDiagnosticSummary(),
            "unsupported",
        )

    try:
        extraction: FileExtraction = pipeline.extract(relative_path, text)
    except Exception as exc:  # noqa: BLE001 — per-file failure must not abort snapshot
        return (
            _text_only_replacement(
                relative_path=relative_path,
                source_hash=source_hash,
                byte_count=byte_count,
                line_count=line_count,
                language=pipeline.language or None,
                parse_status="failed",
                extractor_version=pipeline.extractor_version,
                parse_error=f"{type(exc).__name__}: {exc}",
            ),
            IndexDiagnosticSummary(
                errors=1,
                sample_messages=(f"{relative_path}: {type(exc).__name__}: {exc}",),
            ),
            "failed",
        )

    diag_errors = sum(1 for d in extraction.diagnostics if d.severity == "error")
    diag_warnings = sum(1 for d in extraction.diagnostics if d.severity == "warning")
    diag_infos = sum(1 for d in extraction.diagnostics if d.severity == "info")
    samples = tuple(f"{relative_path}: {d.message}" for d in extraction.diagnostics[:5])
    summary = IndexDiagnosticSummary(
        errors=diag_errors,
        warnings=diag_warnings,
        infos=diag_infos,
        sample_messages=samples,
    )

    parse_error = None
    if extraction.parse_status == "failed":
        parse_error = next(
            (d.message for d in extraction.diagnostics if d.severity == "error"),
            "extraction failed",
        )

    replacement = map_file_extraction(
        extraction,
        source_hash=source_hash,
        byte_count=byte_count,
        line_count=line_count,
        extractor_version=pipeline.extractor_version,
        relative_path=relative_path,
        language=pipeline.language or extraction.language,
        parse_error=parse_error,
    )
    outcome = extraction.parse_status
    if outcome not in {"parsed", "partial", "text_only", "unsupported", "failed"}:
        outcome = "parsed"
    return replacement, summary, outcome


def _merge_diagnostics(
    left: IndexDiagnosticSummary, right: IndexDiagnosticSummary
) -> IndexDiagnosticSummary:
    samples = left.sample_messages + right.sample_messages
    return IndexDiagnosticSummary(
        errors=left.errors + right.errors,
        warnings=left.warnings + right.warnings,
        infos=left.infos + right.infos,
        sample_messages=samples[:12],
    )


def _index_one_file(
    conn: sqlite3.Connection,
    *,
    worktree_id: int,
    snapshot_id: int,
    worktree_root: Path,
    enumerated: EnumeratedFile,
    registry: ExtractorRegistry,
    seen_at: str,
) -> dict[str, object]:
    """Index a single non-ignored file. Returns counter deltas + diagnostics."""
    empty = {
        "reused": 0,
        "parsed": 0,
        "text_only": 0,
        "unsupported": 0,
        "failed": 0,
        "units": 0,
        "imports": 0,
        "references": 0,
        "relationships": 0,
        "resource_links": 0,
        "diagnostics": IndexDiagnosticSummary(),
    }
    relative_path = enumerated.relative_path
    read = _read_source(worktree_root, relative_path)
    if read is None:
        empty["failed"] = 1
        empty["diagnostics"] = IndexDiagnosticSummary(
            errors=1,
            sample_messages=(f"{relative_path}: unreadable or binary",),
        )
        # Still try to record a failed stub when we can hash nothing useful.
        return empty

    raw, text, line_count = read
    source_hash = hash_source_bytes(raw)
    byte_count = len(raw)

    if enumerated.classification == FileClass.TEXT_ONLY:
        extractor_version = TEXT_ONLY_EXTRACTOR_VERSION
        language = enumerated.language
        parse_status: ParseStatus = "text_only"
        need_extract = False
    else:
        # INDEXABLE — select pipeline version for reuse key before extracting.
        pipeline = registry.select(relative_path, source=text)
        if pipeline is None:
            extractor_version = UNSUPPORTED_EXTRACTOR_VERSION
            language = enumerated.language
            parse_status = "unsupported"
            need_extract = False
        else:
            extractor_version = pipeline.extractor_version
            language = pipeline.language or enumerated.language
            parse_status = "parsed"
            need_extract = True

    file_rec = get_or_create_file(
        conn,
        worktree_id=worktree_id,
        relative_path=relative_path,
        seen_at=seen_at,
    )
    existing = _find_file_version(
        conn,
        file_id=file_rec.file_id,
        source_hash=source_hash,
        extractor_version=extractor_version,
    )
    if existing is not None:
        attach_file_to_snapshot(
            conn,
            snapshot_id=snapshot_id,
            file_id=file_rec.file_id,
            file_version_id=existing.file_version_id,
        )
        empty["reused"] = 1
        return empty

    # Cache miss — extract or write text-only / unsupported stub.
    if need_extract:
        replacement, diagnostics, outcome = _extract_structural(
            relative_path=relative_path,
            text=text,
            source_hash=source_hash,
            byte_count=byte_count,
            line_count=line_count,
            registry=registry,
        )
        empty["diagnostics"] = diagnostics
        if outcome in {"parsed", "partial"}:
            empty["parsed"] = 1
        elif outcome == "text_only":
            empty["text_only"] = 1
        elif outcome == "unsupported":
            empty["unsupported"] = 1
        else:
            empty["failed"] = 1
    else:
        replacement = _text_only_replacement(
            relative_path=relative_path,
            source_hash=source_hash,
            byte_count=byte_count,
            line_count=line_count,
            language=language,
            parse_status=parse_status,
            extractor_version=extractor_version,
        )
        if parse_status == "text_only":
            empty["text_only"] = 1
        else:
            empty["unsupported"] = 1

    replace_file_extraction(
        conn,
        snapshot_id=snapshot_id,
        worktree_id=worktree_id,
        extraction=replacement,
        seen_at=seen_at,
    )
    empty["units"] = len(replacement.units)
    empty["imports"] = len(replacement.imports)
    empty["references"] = len(replacement.references)
    empty["relationships"] = len(replacement.relationships)
    empty["resource_links"] = len(replacement.resource_links)
    return empty


def _index_worktree_sync(
    repository_root: Path,
    worktree_root: Path,
    *,
    state_timestamp: str,
    commit_sha: str | None,
    size_policy: SizePolicy,
    registry: ExtractorRegistry,
    conn: sqlite3.Connection | None,
) -> IndexResult:
    owns_conn = conn is None
    if owns_conn:
        conn = open_context_index(repository_root)

    assert conn is not None
    snapshot_id = -1
    worktree_id = -1
    try:
        worktree = get_or_create_worktree(
            conn,
            repository_root=repository_root,
            worktree_root=worktree_root,
            seen_at=state_timestamp,
        )
        worktree_id = worktree.worktree_id
        snapshot = create_building_snapshot(
            conn,
            worktree_id=worktree_id,
            state_timestamp=state_timestamp,
            commit_sha=commit_sha,
            generated_at=state_timestamp,
        )
        snapshot_id = snapshot.snapshot_id

        # This sync body runs in a worker thread (or directly); safe to use
        # asyncio.run for git subprocess enumeration.
        enumerated = asyncio.run(
            enumerate_worktree_files(
                worktree_root,
                size_policy=size_policy,
                registry=registry,
            )
        )

        counters = {
            "discovered": 0,
            "reused": 0,
            "parsed": 0,
            "text_only": 0,
            "unsupported": 0,
            "failed": 0,
            "ignored": 0,
            "units": 0,
            "imports": 0,
            "references": 0,
            "relationships": 0,
            "resource_links": 0,
        }
        diagnostics = IndexDiagnosticSummary()

        for item in enumerated:
            if item.classification == FileClass.IGNORED:
                counters["ignored"] += 1
                continue
            counters["discovered"] += 1
            delta = _index_one_file(
                conn,
                worktree_id=worktree_id,
                snapshot_id=snapshot_id,
                worktree_root=worktree_root.resolve(),
                enumerated=item,
                registry=registry,
                seen_at=state_timestamp,
            )
            for key in (
                "reused",
                "parsed",
                "text_only",
                "unsupported",
                "failed",
                "units",
                "imports",
                "references",
                "relationships",
                "resource_links",
            ):
                value = delta[key]
                if isinstance(value, int):
                    counters[key] += value
            diagnostics = _merge_diagnostics(
                diagnostics,
                delta["diagnostics"],  # type: ignore[arg-type]
            )

        with transaction(conn):
            resolution = resolve_snapshot(conn, snapshot_id)

        mark_snapshot_ready(conn, snapshot_id, generated_at=None)
        retention = apply_retention(conn, worktree_id)

        return IndexResult(
            snapshot_id=snapshot_id,
            worktree_id=worktree_id,
            state_timestamp=state_timestamp,
            status="ready",
            files_discovered=counters["discovered"],
            files_reused=counters["reused"],
            files_parsed=counters["parsed"],
            files_text_only=counters["text_only"],
            files_unsupported=counters["unsupported"],
            files_failed=counters["failed"],
            files_ignored=counters["ignored"],
            semantic_units_written=counters["units"],
            imports_written=counters["imports"],
            references_written=counters["references"],
            relationships_written=counters["relationships"] + resolution.relationships_added,
            resource_links_written=counters["resource_links"],
            diagnostics=diagnostics,
            resolution=resolution,
            deleted_snapshots=retention.deleted_snapshots,
            deleted_file_versions=retention.deleted_file_versions,
            deleted_semantic_unit_versions=retention.deleted_semantic_unit_versions,
            deleted_semantic_units=retention.deleted_semantic_units,
            deleted_files=retention.deleted_files,
        )
    except Exception as exc:
        if snapshot_id >= 0:
            try:
                mark_snapshot_failed(
                    conn,
                    snapshot_id,
                    f"{type(exc).__name__}: {exc}",
                )
            except Exception:
                pass
        return IndexResult(
            snapshot_id=snapshot_id if snapshot_id >= 0 else -1,
            worktree_id=worktree_id if worktree_id >= 0 else -1,
            state_timestamp=state_timestamp,
            status="failed",
            failure_reason=f"{type(exc).__name__}: {exc}",
            resolution=ResolutionSummary(),
        )
    finally:
        if owns_conn:
            conn.close()


async def index_worktree(
    repository_root: Path,
    worktree_root: Path,
    *,
    state_timestamp: str,
    commit_sha: str | None = None,
    size_policy: SizePolicy | None = None,
    registry: ExtractorRegistry | None = None,
    conn: sqlite3.Connection | None = None,
) -> IndexResult:
    """Index the current worktree into the experimental context-index DB.

    Lifecycle: get/create worktree → building snapshot → index files →
    repository resolution → mark ready → retain newest two ready → GC.

    On handled failure the building snapshot is marked ``failed`` and prior
    ready snapshots are preserved.
    """
    policy = size_policy or SizePolicy()
    reg = registry if registry is not None else default_registry()
    return await asyncio.to_thread(
        _index_worktree_sync,
        Path(repository_root),
        Path(worktree_root),
        state_timestamp=state_timestamp,
        commit_sha=commit_sha,
        size_policy=policy,
        registry=reg,
        conn=conn,
    )


def index_worktree_sync(
    repository_root: Path,
    worktree_root: Path,
    *,
    state_timestamp: str,
    commit_sha: str | None = None,
    size_policy: SizePolicy | None = None,
    registry: ExtractorRegistry | None = None,
    conn: sqlite3.Connection | None = None,
) -> IndexResult:
    """Synchronous entry point (tests / callers already off the event loop)."""
    policy = size_policy or SizePolicy()
    reg = registry if registry is not None else default_registry()
    return _index_worktree_sync(
        Path(repository_root),
        Path(worktree_root),
        state_timestamp=state_timestamp,
        commit_sha=commit_sha,
        size_policy=policy,
        registry=reg,
        conn=conn,
    )


__all__ = [
    "TEXT_ONLY_EXTRACTOR_VERSION",
    "UNSUPPORTED_EXTRACTOR_VERSION",
    "index_worktree",
    "index_worktree_sync",
]
