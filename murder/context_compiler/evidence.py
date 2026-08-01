"""Deterministic exact-evidence assembly from preselected ranges.

Operates on a ``ContextRequest``, already-selected ranges, prior ledger
entries, and a ``RepositorySourceReader``. Candidate discovery, ranking, and
focused changed-evidence diffs are intentionally out of scope.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone

from murder.context_compiler.models import (
    ChangedEvidenceNotice,
    ContextBrief,
    ContextRequest,
    EvidenceCategory,
    EvidenceLedgerEntry,
    EvidenceSegment,
    LineRange,
    PayloadKind,
    SelectedRange,
)
from murder.context_compiler.ports import RepositorySourceReader
from murder.context_compiler.ranges import clamp_range, normalize_ranges, subtract_ranges
from murder.context_compiler.rendering import extract_source_slice

# Stable category ordering for deterministic brief assembly.
_CATEGORY_ORDER: dict[EvidenceCategory, int] = {
    EvidenceCategory.EDIT_TARGET: 0,
    EvidenceCategory.CONTRACT: 1,
    EvidenceCategory.SUPPORTING_CONTEXT: 2,
    EvidenceCategory.TEST: 3,
    EvidenceCategory.VERIFICATION: 4,
    EvidenceCategory.CURRENT_DIFF: 5,
    EvidenceCategory.OTHER: 6,
}


@dataclass(frozen=True, slots=True)
class ExactEvidenceResult:
    """Kernel output: exact segments plus internal changed-evidence notices."""

    segments: tuple[EvidenceSegment, ...]
    changed_notices: tuple[ChangedEvidenceNotice, ...]
    unresolved_questions: tuple[str, ...] = ()

    def to_brief(self, request: ContextRequest, *, generated_at: datetime) -> ContextBrief:
        """Lift assembly output into a transport-neutral ``ContextBrief``."""
        trace_id = request.trace_id or request.request_id
        return ContextBrief(
            state_timestamp=request.repository_state.state_timestamp,
            generated_timestamp=generated_at,
            recipient_profile=request.recipient_profile,
            evidence_segments=self.segments,
            unresolved_questions=self.unresolved_questions,
            trace_id=trace_id,
        )


def assemble_exact_evidence(
    request: ContextRequest,
    selections: Sequence[SelectedRange],
    prior_evidence: Sequence[EvidenceLedgerEntry],
    reader: RepositorySourceReader,
) -> ExactEvidenceResult:
    """Assemble exact source evidence from preselected ranges.

    Steps:
    1. Group selections by path.
    2. Normalize requested ranges (per metadata group).
    3. Read each file's current source once.
    4. Compare current source hash with relevant prior ledger entries.
    5. Subtract exact known intervals only when the source hash matches.
    6. Render only missing unchanged intervals.
    7. Preserve category, reason, symbol IDs, and request provenance.
    8. Order deterministically by category policy, path, and starting line.
    9. Emit no segment for ranges fully covered by unchanged prior evidence.

    When prior evidence overlaps but has a different source hash, emit an
    internal ``changed evidence requires refresh/diff`` notice and do not treat
    those prior intervals as known current evidence.
    """
    by_path: dict[str, list[SelectedRange]] = defaultdict(list)
    for selection in selections:
        by_path[selection.path].append(selection)

    segments: list[EvidenceSegment] = []
    notices: list[ChangedEvidenceNotice] = []

    for path in sorted(by_path):
        path_selections = by_path[path]
        snapshot = reader.read(path)
        prior_for_path = [
            entry
            for entry in prior_evidence
            if entry.path == path and _belongs_to_request(entry, request)
        ]

        matching_known: list[LineRange] = []
        stale_priors: list[EvidenceLedgerEntry] = []
        for entry in prior_for_path:
            if entry.source_hash == snapshot.source_hash:
                matching_known.append(LineRange(entry.start_line, entry.end_line))
            else:
                stale_priors.append(entry)

        # Clamp first so wholly invalid selections fail before merging.
        clamped_selections = [
            SelectedRange(
                path=selection.path,
                line_range=clamp_range(selection.line_range, snapshot.line_count),
                category=selection.category,
                reason=selection.reason,
                symbol_ids=selection.symbol_ids,
                provenance=selection.provenance,
            )
            for selection in path_selections
        ]
        groups = _group_selections(clamped_selections)
        requested_union = normalize_ranges(
            [selection.line_range for selection in clamped_selections]
        )

        for stale in stale_priors:
            stale_range = LineRange(stale.start_line, stale.end_line)
            for req in requested_union:
                overlap = _overlap(req, stale_range)
                if overlap is not None:
                    notices.append(
                        ChangedEvidenceNotice(
                            path=path,
                            prior_hash=stale.source_hash,
                            current_hash=snapshot.source_hash,
                            overlapping_range=overlap,
                        )
                    )

        known = normalize_ranges(matching_known)
        changed = normalize_ranges(
            [
                overlap
                for stale in stale_priors
                for requested in requested_union
                if (
                    overlap := _overlap(
                        requested,
                        LineRange(stale.start_line, stale.end_line),
                    )
                )
                is not None
            ]
        )
        for group in groups:
            # Changed prior evidence needs a focused refresh/diff.  Do not send
            # the overlapping current source as though it were merely missing
            # unchanged evidence; unaffected portions remain valid source
            # segments.
            missing = subtract_ranges(group.ranges, (*known, *changed))
            for piece in missing:
                payload = extract_source_slice(snapshot.text, piece.start_line, piece.end_line)
                segments.append(
                    EvidenceSegment(
                        path=path,
                        start_line=piece.start_line,
                        end_line=piece.end_line,
                        source_hash=snapshot.source_hash,
                        payload_kind=PayloadKind.SOURCE,
                        payload_text=payload,
                        symbol_ids=group.symbol_ids,
                        category=group.category,
                        reason=group.reason,
                        provenance=group.provenance,
                    )
                )

    segments.sort(
        key=lambda seg: (
            _CATEGORY_ORDER.get(seg.category, 99),
            seg.path,
            seg.start_line,
            seg.end_line,
        )
    )
    # Deduplicate identical changed notices while preserving order.
    unique_notices = tuple(dict.fromkeys(notices))
    return ExactEvidenceResult(
        segments=tuple(segments),
        changed_notices=unique_notices,
    )


def build_brief_from_selections(
    request: ContextRequest,
    selections: Sequence[SelectedRange],
    prior_evidence: Sequence[EvidenceLedgerEntry],
    reader: RepositorySourceReader,
    *,
    generated_at: datetime | None = None,
) -> ContextBrief:
    """Convenience: assemble exact evidence and wrap it as a ``ContextBrief``."""
    when = generated_at or datetime.now(timezone.utc)
    result = assemble_exact_evidence(request, selections, prior_evidence, reader)
    return result.to_brief(request, generated_at=when)


@dataclass(frozen=True, slots=True)
class _SelectionGroup:
    category: EvidenceCategory
    reason: str
    symbol_ids: tuple[str, ...]
    provenance: str | None
    ranges: tuple[LineRange, ...]


def _group_selections(selections: Sequence[SelectedRange]) -> tuple[_SelectionGroup, ...]:
    """Group selections that share category/reason/provenance; union symbol IDs."""
    buckets: dict[tuple[EvidenceCategory, str, str | None], list[SelectedRange]] = defaultdict(list)
    for selection in selections:
        key = (selection.category, selection.reason, selection.provenance)
        buckets[key].append(selection)

    groups: list[_SelectionGroup] = []
    for (category, reason, provenance), items in sorted(
        buckets.items(),
        key=lambda item: (
            _CATEGORY_ORDER.get(item[0][0], 99),
            item[0][1],
            item[0][2] or "",
        ),
    ):
        symbol_ids = tuple(sorted({sid for item in items for sid in item.symbol_ids}))
        ranges = normalize_ranges([item.line_range for item in items])
        groups.append(
            _SelectionGroup(
                category=category,
                reason=reason,
                symbol_ids=symbol_ids,
                provenance=provenance,
                ranges=ranges,
            )
        )
    return tuple(groups)


def _overlap(left: LineRange, right: LineRange) -> LineRange | None:
    start = max(left.start_line, right.start_line)
    end = min(left.end_line, right.end_line)
    if end < start:
        return None
    return LineRange(start, end)


def _belongs_to_request(entry: EvidenceLedgerEntry, request: ContextRequest) -> bool:
    """Return whether a supplied ledger entry belongs to this evidence scope.

    Scope is repository/worktree + recipient + session/conversation — not crow
    or agent ID. Only confirmed-supplied entries participate.
    """
    state = request.repository_state
    return (
        entry.is_known()
        and entry.recipient_id == request.recipient_id
        and entry.repository_root == state.repository_root
        and entry.worktree_root == state.worktree_root
        and entry.session_id == request.session_id
        and entry.conversation_id == request.conversation_id
    )
