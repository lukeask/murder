"""Narrow protocols for later context-compiler implementations.

Step 0 defines these ports without wiring persistence, retrieval providers, or
orchestration. Implementations must keep ranking scores, rejected candidates,
graph paths, and model deliberation out of recipient-visible ``ContextBrief``
values — store that material via ``RetrievalTraceStore`` instead.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol

from murder.context_compiler.grading.ports import ContextGrader as ContextGrader  # noqa: PLC0414
from murder.context_compiler.models import (
    CandidateRecord,
    ContextBrief,
    ContextRequest,
    EvidenceLedgerEntry,
    EvidenceScope,
    LedgerEntryDraft,
)


class ContextCompiler(Protocol):
    """Primary public compilation operation."""

    async def compile(self, request: ContextRequest) -> ContextBrief: ...


class CandidateProvider(Protocol):
    """Produce retrieval candidates for a request.

    Lexical, structural, diff, heuristic, and future vector providers share this
    abstraction. Do not add vector-specific methods.
    """

    async def generate(
        self,
        request: ContextRequest,
        prior_evidence: Sequence[EvidenceLedgerEntry],
    ) -> Sequence[CandidateRecord]: ...


class EvidenceLedger(Protocol):
    """Session-local two-phase evidence ledger.

    ``prepare_entries`` records what is about to be sent. Only after the
    consumer confirms delivery may ``mark_supplied`` make those entries
    participate in subtraction. Failed delivery must ``mark_abandoned``.
    """

    def prepare_entries(
        self,
        scope: EvidenceScope,
        drafts: Sequence[LedgerEntryDraft],
    ) -> str:
        """Persist prepared entries. Return a ``delivery_id``."""
        ...

    def mark_supplied(self, delivery_id: str) -> None:
        """Confirm the recipient received this delivery."""
        ...

    def mark_abandoned(self, delivery_id: str) -> None:
        """Discard a delivery that never reached the recipient."""
        ...

    def load_supplied(self, scope: EvidenceScope) -> Sequence[EvidenceLedgerEntry]:
        """Load confirmed-supplied entries for ``scope`` (with blob text)."""
        ...

    def cleanup_scope(self, scope: EvidenceScope) -> int:
        """Delete all ledger rows for ``scope``. GC orphaned blobs. Returns rows deleted."""
        ...

    def cleanup_session(self, session_id: str) -> int:
        """Delete every scope tied to ``session_id``. GC orphaned blobs."""
        ...

    def cleanup_abandoned_deliveries(self) -> int:
        """Remove abandoned entries and GC blobs. Returns entries deleted."""
        ...

    def cleanup_repository(self, repository_root: Path) -> int:
        """Remove all scopes for ``repository_root``. GC orphaned blobs."""
        ...


class RetrievalTraceStore(Protocol):
    """Store internal retrieval/debug information separately from ``ContextBrief``."""

    async def store(self, trace_id: str, payload: Mapping[str, object]) -> None: ...


class SourceSnapshot(Protocol):
    """Current source text, content hash, and line count for one path."""

    @property
    def text(self) -> str: ...

    @property
    def source_hash(self) -> str: ...

    @property
    def line_count(self) -> int: ...


class RepositorySourceReader(Protocol):
    """Read current source from the request's actual worktree."""

    def read(self, relative_path: str) -> SourceSnapshot: ...
