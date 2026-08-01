"""Immutable domain records for the context compiler exact-evidence kernel.

These types are transport-neutral and invocation-neutral. They intentionally
omit ranking scores, rejected candidates, graph paths, model deliberation, and
token-budget narration — those belong in retrieval-trace storage, not in
recipient-visible briefs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path


class RecipientProfile(str, Enum):
    """Policy input describing the evidence consumer.

    Profiles do not encode fixed graph-hop counts or mandatory evidence sections.
    """

    COMPACT = "compact"
    PLANNING = "planning"
    IMPLEMENTATION = "implementation"


class EvidenceCategory(str, Enum):
    """Extensible evidence distinctions shared across recipient profiles."""

    EDIT_TARGET = "edit_target"
    CONTRACT = "contract"
    SUPPORTING_CONTEXT = "supporting_context"
    TEST = "test"
    VERIFICATION = "verification"
    CURRENT_DIFF = "current_diff"
    OTHER = "other"


class PayloadKind(str, Enum):
    """Authoritative evidence payload kinds.

    ``source`` is exact numbered excerpts. ``diff`` is the focused changed-
    evidence format produced by the Step 6 ledger when a prior supplied
    excerpt's source hash no longer matches.
    """

    SOURCE = "source"
    DIFF = "diff"


class EvidenceLedgerStatus(str, Enum):
    """Two-phase delivery status for persisted ledger entries.

    Only ``supplied`` participates in subtraction. ``prepared`` is invisible
    until confirmed; ``abandoned`` never becomes known.
    """

    PREPARED = "prepared"
    SUPPLIED = "supplied"
    ABANDONED = "abandoned"


@dataclass(frozen=True, slots=True)
class RepositoryState:
    """Repository state against which evidence is compiled.

    ``repository_root`` and ``worktree_root`` are distinct: multiple agents may
    share one worktree, and a crow does not exclusively own a worktree.
    ``state_timestamp`` is provenance only — not a fencing token.
    """

    repository_root: Path
    worktree_root: Path
    state_timestamp: datetime
    commit_sha: str | None = None


@dataclass(frozen=True, slots=True)
class ContextRequest:
    """Invocation-neutral compilation request.

    Do not add transport-specific booleans such as ``is_compact_command`` or
    ``is_startup``. ``invocation_purpose`` is optional observability only and
    must not control core evidence semantics.
    """

    request_id: str
    recipient_id: str
    repository_state: RepositoryState
    objective: str
    recipient_profile: RecipientProfile
    agent_id: str | None = None
    session_id: str | None = None
    conversation_id: str | None = None
    first_message: str | None = None
    max_tokens: int | None = None
    path_hints: tuple[str, ...] = ()
    symbol_hints: tuple[str, ...] = ()
    # Lexical terms for providers (distinct from symbol_hints — Step 5 gaps).
    search_terms: tuple[str, ...] = ()
    # Prefer these relationship kinds on Step 4 expansion / structural neighbors.
    relationship_kind_hints: tuple[str, ...] = ()
    invocation_purpose: str | None = None
    trace_id: str | None = None


@dataclass(frozen=True, slots=True)
class LineRange:
    """One-based, inclusive line range."""

    start_line: int
    end_line: int

    def __post_init__(self) -> None:
        if self.start_line <= 0 or self.end_line <= 0:
            raise ValueError(
                f"LineRange endpoints must be positive; got {self.start_line}-{self.end_line}"
            )
        if self.end_line < self.start_line:
            raise ValueError(
                f"LineRange end_line ({self.end_line}) must be >= start_line ({self.start_line})"
            )


@dataclass(frozen=True, slots=True)
class SelectedRange:
    """Already-selected source range handed to the exact-evidence kernel.

    This is the boundary between later retrieval/ranking and deterministic
    assembly/rendering.
    """

    path: str
    line_range: LineRange
    category: EvidenceCategory
    reason: str
    symbol_ids: tuple[str, ...] = ()
    provenance: str | None = None


@dataclass(frozen=True, slots=True)
class EvidenceSegment:
    """Final authoritative evidence segment for a recipient brief."""

    path: str
    start_line: int
    end_line: int
    source_hash: str
    payload_kind: PayloadKind
    payload_text: str
    symbol_ids: tuple[str, ...]
    category: EvidenceCategory
    reason: str
    provenance: str | None = None


@dataclass(frozen=True, slots=True)
class ContextBrief:
    """Transport-neutral compilation result.

    The primary representation is typed evidence segments, not a pre-rendered
    Markdown string. Rendering may group segments by category later.
    """

    state_timestamp: datetime
    generated_timestamp: datetime
    recipient_profile: RecipientProfile
    evidence_segments: tuple[EvidenceSegment, ...]
    unresolved_questions: tuple[str, ...]
    trace_id: str


@dataclass(frozen=True, slots=True)
class EvidenceScope:
    """Session-local ledger scope — never crow-ID keyed.

    Identity is repository/worktree + recipient + session or conversation.
    Nothing here is shared across sessions.
    """

    repository_root: Path
    worktree_root: Path
    recipient_id: str
    session_id: str | None = None
    conversation_id: str | None = None


@dataclass(frozen=True, slots=True)
class EvidenceLedgerEntry:
    """Agent-local evidence record for deterministic subtraction and diffs.

    Two shapes share this type (adapt, do not duplicate):

    * **Persisted ledger (Step 6):** ``content_hash``, ``category``,
      ``payload_kind``, ``status``, ``delivery_id``, ``entry_id``,
      ``prepared_at`` / ``supplied_at``, and ``payload_text`` are authoritative.
      Scope identity comes from ``recipient_id``, repository/worktree roots,
      and ``session_id`` / ``conversation_id``.
    * **Step 0 assembly:** ``reason``, ``recipient_profile``, and
      ``operation_id`` are request-side provenance for in-memory kernel calls.
      They are **not** ledger columns. Ledger loads leave them at empty /
      default placeholders — use ``category`` and ``delivery_id`` instead.

    Cut Step-0 flags (``later_opened``, ``later_edited``,
    ``diagnostic_implication``) have no writer and no reader in the ledger
    path; they remain defaulted ``False`` for binary compatibility with Step 0
    constructions only.
    """

    recipient_id: str
    repository_root: Path
    worktree_root: Path
    state_timestamp: datetime
    source_hash: str
    path: str
    start_line: int
    end_line: int
    reason: str = ""
    recipient_profile: RecipientProfile = RecipientProfile.IMPLEMENTATION
    operation_id: str = ""
    agent_id: str | None = None
    session_id: str | None = None
    conversation_id: str | None = None
    symbol_ids: tuple[str, ...] = ()
    supplied: bool = True
    # Cut Step-0 flags — unused by the ledger; always False on load.
    later_opened: bool = False
    later_edited: bool = False
    diagnostic_implication: bool = False
    # Persisted Step 6 fields (optional on in-memory Step 0 constructions).
    content_hash: str | None = None
    category: EvidenceCategory | None = None
    payload_kind: PayloadKind = PayloadKind.SOURCE
    status: EvidenceLedgerStatus | None = None
    delivery_id: str | None = None
    entry_id: int | None = None
    prepared_at: datetime | None = None
    supplied_at: datetime | None = None
    # Exact bounded excerpt when loaded from ``evidence_blobs`` (for diffs).
    payload_text: str | None = None

    def is_known(self) -> bool:
        """Return True only when this entry may participate in subtraction."""
        if self.status is not None:
            return self.status is EvidenceLedgerStatus.SUPPLIED
        return self.supplied


@dataclass(frozen=True, slots=True)
class LedgerEntryDraft:
    """Exact excerpt ready for two-phase ``prepare_entries``."""

    path: str
    start_line: int
    end_line: int
    source_hash: str
    text: str
    category: EvidenceCategory
    payload_kind: PayloadKind = PayloadKind.SOURCE


@dataclass(frozen=True, slots=True)
class ChangedEvidenceNotice:
    """Internal notice that prior evidence is stale under a new source hash.

    When prior excerpt text is available the ledger emits a focused diff
    segment; otherwise the current source is sent and this notice records
    ``changed_evidence_unmappable``.
    """

    path: str
    prior_hash: str
    current_hash: str
    overlapping_range: LineRange
    message: str = "changed evidence requires refresh/diff"


@dataclass(frozen=True, slots=True)
class DeletionNotice:
    """Recipient-facing notice that previously supplied evidence no longer exists.

    Names the old path and range. Never silently drops known evidence.
    """

    path: str
    start_line: int
    end_line: int
    source_hash: str

    def render(self) -> str:
        """Render without internal IDs — path and range only."""
        return f"deleted: {self.path}:{self.start_line}-{self.end_line}\n"


@dataclass(frozen=True, slots=True)
class CandidateRecord:
    """Minimal retrieval candidate; providers may attach provenance and scores.

    Scores and rejection detail must not leak into recipient-visible models.
    """

    path: str
    line_range: LineRange | None = None
    category: EvidenceCategory | None = None
    reason: str | None = None
    symbol_ids: tuple[str, ...] = ()
    provenance: str | None = None
    score: float | None = None
    metadata: tuple[tuple[str, str], ...] = field(default_factory=tuple)
