"""Plan recipient evidence from current selections and supplied ledger entries.

Extends the Step 0 exact-evidence kernel with focused diffs and deletion
notices. Only ``supplied`` prior evidence participates.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from murder.context_compiler.ledger.diff import build_focused_diff
from murder.context_compiler.models import (
    ChangedEvidenceNotice,
    DeletionNotice,
    EvidenceCategory,
    EvidenceLedgerEntry,
    EvidenceSegment,
    LedgerEntryDraft,
    LineRange,
    PayloadKind,
    SelectedRange,
)
from murder.context_compiler.ports import RepositorySourceReader, SourceSnapshot
from murder.context_compiler.ranges import clamp_range, normalize_ranges, subtract_ranges
from murder.context_compiler.rendering import extract_source_slice
from murder.context_compiler.source import SourceReadError

# Stable category ordering for deterministic plan assembly (mirrors evidence.py).
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
class LedgerPlanResult:
    """Recipient-facing plan: missing source, focused diffs, deletion notices.

    Internal IDs never appear in rendered payloads. ``unmappable_paths`` records
    ``changed_evidence_unmappable`` events for traces.
    """

    segments: tuple[EvidenceSegment, ...]
    deletion_notices: tuple[DeletionNotice, ...]
    changed_notices: tuple[ChangedEvidenceNotice, ...]
    unmappable_paths: tuple[str, ...] = ()


def plan_evidence(
    selections: Sequence[SelectedRange],
    prior_supplied: Sequence[EvidenceLedgerEntry],
    reader: RepositorySourceReader,
) -> LedgerPlanResult:
    """Apply ledger semantics to current selections.

    * Matching ``source_hash``: subtract known intervals; emit missing source.
    * Changed ``source_hash``: focused diff of prior text vs current range.
    * Prior supplied with no current counterpart on a changed/missing path:
      deletion notice.
    * Unmappable changed evidence: send current source; record unmappable path.
    """
    known_prior = tuple(entry for entry in prior_supplied if entry.is_known())
    by_path: dict[str, list[SelectedRange]] = defaultdict(list)
    for selection in selections:
        by_path[selection.path].append(selection)

    prior_by_path: dict[str, list[EvidenceLedgerEntry]] = defaultdict(list)
    for entry in known_prior:
        prior_by_path[entry.path].append(entry)

    segments: list[EvidenceSegment] = []
    notices: list[ChangedEvidenceNotice] = []
    deletions: list[DeletionNotice] = []
    unmappable: list[str] = []

    selection_paths = set(by_path)
    deletions.extend(_deletions_for_missing_files(prior_by_path, selection_paths, reader))

    for path in sorted(by_path):
        path_result = _plan_path(path, by_path[path], prior_by_path.get(path, []), reader)
        segments.extend(path_result.segments)
        notices.extend(path_result.notices)
        deletions.extend(path_result.deletions)
        unmappable.extend(path_result.unmappable)

    segments.sort(
        key=lambda seg: (
            _CATEGORY_ORDER.get(seg.category, 99),
            seg.path,
            seg.start_line,
            seg.end_line,
            seg.payload_kind.value,
        )
    )
    return LedgerPlanResult(
        segments=tuple(segments),
        deletion_notices=tuple(deletions),
        changed_notices=tuple(dict.fromkeys(notices)),
        unmappable_paths=tuple(dict.fromkeys(unmappable)),
    )


@dataclass(frozen=True, slots=True)
class _PathPlan:
    segments: tuple[EvidenceSegment, ...]
    notices: tuple[ChangedEvidenceNotice, ...]
    deletions: tuple[DeletionNotice, ...]
    unmappable: tuple[str, ...]


def _plan_path(
    path: str,
    path_selections: Sequence[SelectedRange],
    priors: Sequence[EvidenceLedgerEntry],
    reader: RepositorySourceReader,
) -> _PathPlan:
    snapshot = reader.read(path)
    matching = [e for e in priors if e.source_hash == snapshot.source_hash]
    stale = [e for e in priors if e.source_hash != snapshot.source_hash]
    clamped = tuple(
        SelectedRange(
            path=selection.path,
            line_range=clamp_range(selection.line_range, snapshot.line_count),
            category=selection.category,
            reason=selection.reason,
            symbol_ids=selection.symbol_ids,
            provenance=selection.provenance,
        )
        for selection in path_selections
    )

    if matching and not stale:
        return _PathPlan(
            segments=_missing_source_segments(path, clamped, matching, snapshot),
            notices=(),
            deletions=(),
            unmappable=(),
        )
    if not stale:
        return _PathPlan(
            segments=_full_source_segments(path, clamped, snapshot),
            notices=(),
            deletions=(),
            unmappable=(),
        )
    return _plan_changed_path(path, clamped, stale, snapshot)


def _missing_source_segments(
    path: str,
    clamped: Sequence[SelectedRange],
    matching: Sequence[EvidenceLedgerEntry],
    snapshot: SourceSnapshot,
) -> tuple[EvidenceSegment, ...]:
    known_ranges = normalize_ranges([LineRange(e.start_line, e.end_line) for e in matching])
    segments: list[EvidenceSegment] = []
    for selection in clamped:
        for piece in subtract_ranges([selection.line_range], known_ranges):
            segments.append(
                EvidenceSegment(
                    path=path,
                    start_line=piece.start_line,
                    end_line=piece.end_line,
                    source_hash=snapshot.source_hash,
                    payload_kind=PayloadKind.SOURCE,
                    payload_text=extract_source_slice(
                        snapshot.text, piece.start_line, piece.end_line
                    ),
                    symbol_ids=selection.symbol_ids,
                    category=selection.category,
                    reason=selection.reason,
                    provenance=selection.provenance,
                )
            )
    return tuple(segments)


def _full_source_segments(
    path: str,
    clamped: Sequence[SelectedRange],
    snapshot: SourceSnapshot,
) -> tuple[EvidenceSegment, ...]:
    return tuple(
        EvidenceSegment(
            path=path,
            start_line=selection.line_range.start_line,
            end_line=selection.line_range.end_line,
            source_hash=snapshot.source_hash,
            payload_kind=PayloadKind.SOURCE,
            payload_text=extract_source_slice(
                snapshot.text,
                selection.line_range.start_line,
                selection.line_range.end_line,
            ),
            symbol_ids=selection.symbol_ids,
            category=selection.category,
            reason=selection.reason,
            provenance=selection.provenance,
        )
        for selection in clamped
    )


def _plan_changed_path(
    path: str,
    clamped: Sequence[SelectedRange],
    stale: Sequence[EvidenceLedgerEntry],
    snapshot: SourceSnapshot,
) -> _PathPlan:
    segments: list[EvidenceSegment] = []
    notices: list[ChangedEvidenceNotice] = []
    unmappable: list[str] = []
    covered_priors: set[int] = set()

    for selection in clamped:
        prior = _best_prior_for_selection(selection.line_range, stale)
        current_text = extract_source_slice(
            snapshot.text,
            selection.line_range.start_line,
            selection.line_range.end_line,
        )
        if prior is None or prior.payload_text is None:
            prior_hash = stale[0].source_hash if prior is None else prior.source_hash
            notices.append(
                ChangedEvidenceNotice(
                    path=path,
                    prior_hash=prior_hash,
                    current_hash=snapshot.source_hash,
                    overlapping_range=selection.line_range,
                    message="changed_evidence_unmappable",
                )
            )
            unmappable.append(path)
            segments.append(
                EvidenceSegment(
                    path=path,
                    start_line=selection.line_range.start_line,
                    end_line=selection.line_range.end_line,
                    source_hash=snapshot.source_hash,
                    payload_kind=PayloadKind.SOURCE,
                    payload_text=current_text,
                    symbol_ids=selection.symbol_ids,
                    category=selection.category,
                    reason=selection.reason,
                    provenance=selection.provenance,
                )
            )
            continue

        covered_priors.add(id(prior))
        diff = build_focused_diff(
            path=path,
            old_range=LineRange(prior.start_line, prior.end_line),
            old_text=prior.payload_text,
            new_range=selection.line_range,
            new_text=current_text,
        )
        if diff.unmappable:
            notices.append(
                ChangedEvidenceNotice(
                    path=path,
                    prior_hash=prior.source_hash,
                    current_hash=snapshot.source_hash,
                    overlapping_range=selection.line_range,
                    message="changed_evidence_unmappable",
                )
            )
            unmappable.append(path)
            segments.append(
                EvidenceSegment(
                    path=path,
                    start_line=selection.line_range.start_line,
                    end_line=selection.line_range.end_line,
                    source_hash=snapshot.source_hash,
                    payload_kind=PayloadKind.SOURCE,
                    payload_text=current_text,
                    symbol_ids=selection.symbol_ids,
                    category=selection.category,
                    reason=selection.reason,
                    provenance=selection.provenance,
                )
            )
            continue

        overlap = _overlap(
            selection.line_range,
            LineRange(prior.start_line, prior.end_line),
        )
        notices.append(
            ChangedEvidenceNotice(
                path=path,
                prior_hash=prior.source_hash,
                current_hash=snapshot.source_hash,
                overlapping_range=overlap or selection.line_range,
            )
        )
        segments.append(
            EvidenceSegment(
                path=path,
                start_line=selection.line_range.start_line,
                end_line=selection.line_range.end_line,
                source_hash=snapshot.source_hash,
                payload_kind=PayloadKind.DIFF,
                payload_text=diff.text,
                symbol_ids=selection.symbol_ids,
                category=selection.category,
                reason=selection.reason,
                provenance=selection.provenance,
            )
        )

    deletions = tuple(
        DeletionNotice(
            path=entry.path,
            start_line=entry.start_line,
            end_line=entry.end_line,
            source_hash=entry.source_hash,
        )
        for entry in stale
        if id(entry) not in covered_priors
        and not any(
            _overlap(sel.line_range, LineRange(entry.start_line, entry.end_line)) is not None
            for sel in clamped
        )
    )
    return _PathPlan(
        segments=tuple(segments),
        notices=tuple(notices),
        deletions=deletions,
        unmappable=tuple(unmappable),
    )


def drafts_from_segments(segments: Sequence[EvidenceSegment]) -> tuple[LedgerEntryDraft, ...]:
    """Build prepare drafts from planned segments (exact text that will be sent).

    Diff payloads store the diff text as the blob so restarts still know what
    the recipient saw; source payloads store the excerpt.
    """
    return tuple(
        LedgerEntryDraft(
            path=segment.path,
            start_line=segment.start_line,
            end_line=segment.end_line,
            source_hash=segment.source_hash,
            text=segment.payload_text,
            category=segment.category,
            payload_kind=segment.payload_kind,
        )
        for segment in segments
    )


def _deletions_for_missing_files(
    prior_by_path: dict[str, list[EvidenceLedgerEntry]],
    selection_paths: set[str],
    reader: RepositorySourceReader,
) -> list[DeletionNotice]:
    deletions: list[DeletionNotice] = []
    for path, priors in sorted(prior_by_path.items()):
        if path in selection_paths:
            continue
        if not _file_missing(reader, path):
            continue
        for entry in priors:
            deletions.append(
                DeletionNotice(
                    path=entry.path,
                    start_line=entry.start_line,
                    end_line=entry.end_line,
                    source_hash=entry.source_hash,
                )
            )
    return deletions


def _best_prior_for_selection(
    selection: LineRange,
    stale: Sequence[EvidenceLedgerEntry],
) -> EvidenceLedgerEntry | None:
    """Prefer overlapping prior ranges; else the single stale entry if only one."""
    overlapping = [
        entry
        for entry in stale
        if _overlap(selection, LineRange(entry.start_line, entry.end_line)) is not None
    ]
    if overlapping:
        return sorted(
            overlapping,
            key=lambda e: (e.start_line, -(e.end_line - e.start_line), e.entry_id or 0),
        )[0]
    if len(stale) == 1:
        return stale[0]
    return None


def _overlap(left: LineRange, right: LineRange) -> LineRange | None:
    start = max(left.start_line, right.start_line)
    end = min(left.end_line, right.end_line)
    if end < start:
        return None
    return LineRange(start, end)


def _file_missing(reader: RepositorySourceReader, path: str) -> bool:
    try:
        reader.read(path)
    except (SourceReadError, OSError, ValueError):
        return True
    return False


__all__ = [
    "LedgerPlanResult",
    "drafts_from_segments",
    "plan_evidence",
]
