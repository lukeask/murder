"""Murder context compiler — exact-evidence foundation (Step 0).

This package is the standalone substrate for Murder's replacement codebase-
context system. Its public operation is conceptually::

    await compiler.compile(request) -> ContextBrief

Step 0 establishes the domain contracts and the deterministic exact-source
kernel behind that operation. It does **not** implement a working retrieval
system.

What this package provides
--------------------------
* Immutable domain models (``ContextRequest``, ``ContextBrief``,
  ``EvidenceSegment``, ``EvidenceLedgerEntry``, …).
* Narrow ports for later providers, ledger persistence, retrieval traces, and
  the async ``ContextCompiler`` facade.
* Pure inclusive line-range normalization and subtraction.
* A worktree-bound filesystem source reader with SHA-256 hashing and path
  escape rejection.
* Deterministic assembly of preselected ranges into exact numbered source
  evidence, with unchanged prior-evidence subtraction and changed-hash notices.
* Exact source rendering (``path:start-end`` + numbered lines).
* Experimental context-index persistence (``.murder/context-index.db``):
  worktree/snapshot lifecycle, content-addressed file versions, semantic
  units, imports/references/relationships/resource links, two-ready
  retention, and garbage collection (see ``murder.context_compiler.persistence``).
* Agent-local evidence ledger (Step 6): session-scoped two-phase delivery,
  content-addressed excerpt blobs, focused changed-evidence diffs, and
  deletion notices.

Intentionally deferred to later parts
-------------------------------------
* PageRank, vector search, and LangGraph orchestration.
* Integration with ``BriefAssembler``, CodingCrow, startup briefing, turn
  expansion, and custom ``/compact`` command handling (Steps 7–8).
* Retrieval-trace persistence beyond the grading/ranking in-memory traces.
* Token-budget defaults and recipient-facing synthesis separate from exact
  evidence.

Intentionally deferred (historical Steps 2–5 notes retained below)
------------------------------------------------------------------
* Candidate discovery (lexical, structural, diff, heuristic, vector) — landed.
* Tree-sitter / language extractors and structural indexing — landed.
* Profile-specific ranking weights — landed (no PageRank).
* LLM grading and adequacy — landed.
* Focused diff formatting for changed known evidence — landed (Step 6).
* Evidence-ledger persistence — landed (Step 6).

Integration boundary
--------------------
Later orchestration should call an async collaborator before synchronous prompt
assembly, approximately::

    compiled = await context_service.compile(...)
    startup_prompt = brief_service.build(..., compiled_context=compiled)

This package must not depend on ``murder.codebase_map``, ``.murder/map/``,
runtime agents, a particular LLM provider, persistence backend, or invocation
mode. A custom ``/compact`` consumer may call the compiler and consume
``ContextBrief``, but the compiler itself must not summarize conversation
history, mutate sessions, send terminal input, or invoke harness-native compact
commands.
"""

from __future__ import annotations

from murder.context_compiler.evidence import (
    ExactEvidenceResult,
    assemble_exact_evidence,
    build_brief_from_selections,
)
from murder.context_compiler.ledger import FocusedDiffResult, build_focused_diff
from murder.context_compiler.ledger.plan import (
    LedgerPlanResult,
    drafts_from_segments,
    plan_evidence,
)
from murder.context_compiler.models import (
    CandidateRecord,
    ChangedEvidenceNotice,
    ContextBrief,
    ContextRequest,
    DeletionNotice,
    EvidenceCategory,
    EvidenceLedgerEntry,
    EvidenceLedgerStatus,
    EvidenceScope,
    EvidenceSegment,
    LedgerEntryDraft,
    LineRange,
    PayloadKind,
    RecipientProfile,
    RepositoryState,
    SelectedRange,
)
from murder.context_compiler.persistence.evidence_ledger import SqliteEvidenceLedger
from murder.context_compiler.ports import (
    CandidateProvider,
    ContextCompiler,
    ContextGrader,
    EvidenceLedger,
    RepositorySourceReader,
    RetrievalTraceStore,
    SourceSnapshot,
)
from murder.context_compiler.ranges import (
    RangeValidationError,
    clamp_range,
    normalize_ranges,
    subtract_ranges,
)
from murder.context_compiler.rendering import (
    RenderError,
    extract_source_slice,
    render_deletion_notice,
    render_diff_segment,
    render_evidence_segment,
    render_source_segment,
)
from murder.context_compiler.source import (
    FileSourceSnapshot,
    FilesystemSourceReader,
    SourceReadError,
    count_source_lines,
    hash_source_bytes,
    resolve_worktree_path,
)

__all__ = [
    "CandidateProvider",
    "CandidateRecord",
    "ChangedEvidenceNotice",
    "ContextBrief",
    "ContextCompiler",
    "ContextGrader",
    "ContextRequest",
    "DeletionNotice",
    "EvidenceCategory",
    "EvidenceLedger",
    "EvidenceLedgerEntry",
    "EvidenceLedgerStatus",
    "EvidenceScope",
    "EvidenceSegment",
    "ExactEvidenceResult",
    "FileSourceSnapshot",
    "FilesystemSourceReader",
    "FocusedDiffResult",
    "LedgerEntryDraft",
    "LedgerPlanResult",
    "LineRange",
    "PayloadKind",
    "RangeValidationError",
    "RecipientProfile",
    "RenderError",
    "RepositorySourceReader",
    "RepositoryState",
    "RetrievalTraceStore",
    "SelectedRange",
    "SourceReadError",
    "SourceSnapshot",
    "SqliteEvidenceLedger",
    "assemble_exact_evidence",
    "build_brief_from_selections",
    "build_focused_diff",
    "clamp_range",
    "count_source_lines",
    "drafts_from_segments",
    "extract_source_slice",
    "hash_source_bytes",
    "normalize_ranges",
    "plan_evidence",
    "render_deletion_notice",
    "render_diff_segment",
    "render_evidence_segment",
    "render_source_segment",
    "resolve_worktree_path",
    "subtract_ranges",
]
