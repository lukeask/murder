"""Canonical frame-to-evidence-to-observation projection runtime."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from murder.llm.harness_control.model.evidence import EvidenceEnvelope, FrameId
from murder.llm.harness_control.model.observations import (
    ObservationDelta,
    ObservationRevision,
    ObservationSnapshot,
)


class ObservationStore:
    """Applies immutable deltas and only advances semantic sequence on change."""

    def __init__(self, initial: ObservationSnapshot) -> None:
        self._snapshot = initial
        self._history: list[ObservationSnapshot] = [initial]

    @property
    def latest(self) -> ObservationSnapshot:
        return self._snapshot

    @property
    def history(self) -> tuple[ObservationSnapshot, ...]:
        return tuple(self._history)

    def apply(
        self,
        delta: ObservationDelta,
        *,
        captured_at: datetime,
        pane_epoch: int,
        capture_sequence: int,
    ) -> ObservationSnapshot:
        current = self._snapshot
        changed = any(
            _semantic_value(observed) != _semantic_value(getattr(current, name))
            for name, observed in delta.updates.items()
        )
        revision = ObservationRevision(
            pane_epoch=pane_epoch,
            capture_sequence=capture_sequence,
            semantic_sequence=current.revision.semantic_sequence + int(changed),
        )
        # A projection must never smuggle a revision created for a different frame.
        updates = {
            name: replace(observed, revision=revision, observed_at=captured_at)
            for name, observed in delta.updates.items()
        }
        self._snapshot = replace(current, revision=revision, captured_at=captured_at, **updates)
        self._history.append(self._snapshot)
        return self._snapshot


def _semantic_value(observed: object) -> object:
    """Exclude capture provenance from semantic revision comparisons."""

    return (
        getattr(observed, "knowledge", None),
        getattr(observed, "value", None),
    )


class EvidenceHistory:
    """Append-only evidence history used by parsers and durable persistence."""

    def __init__(self) -> None:
        self._items: list[EvidenceEnvelope] = []

    def append(self, envelopes: tuple[EvidenceEnvelope, ...] | list[EvidenceEnvelope]) -> None:
        known = {item.evidence_id for item in self._items}
        self._items.extend(item for item in envelopes if item.evidence_id not in known)

    def all(self) -> tuple[EvidenceEnvelope, ...]:
        return tuple(self._items)

    def for_frame(self, frame_id: FrameId) -> tuple[EvidenceEnvelope, ...]:
        return tuple(item for item in self._items if item.frame_id == frame_id)
