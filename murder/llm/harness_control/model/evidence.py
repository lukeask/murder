"""Immutable raw-frame and broad harness-evidence records.

Evidence is intentionally not a giant cross-harness pane model.  ``payload``
belongs to a harness-specific schema and remains durable even when only part
of it is promoted into shared observations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, NewType

FrameId = NewType("FrameId", str)
EvidenceId = NewType("EvidenceId", str)
HarnessId = NewType("HarnessId", str)


@dataclass(frozen=True, slots=True)
class TerminalFrame:
    frame_id: FrameId
    harness_id: HarnessId
    captured_at: datetime
    width: int
    height: int
    raw_text: str
    ansi_preserved: bool
    pane_epoch: int
    capture_sequence: int
    viewport_text: str | None = None

    def __post_init__(self) -> None:
        if self.width < 0 or self.height < 0:
            raise ValueError("terminal dimensions cannot be negative")
        if self.capture_sequence < 0 or self.pane_epoch < 0:
            raise ValueError("frame revisions cannot be negative")


@dataclass(frozen=True, slots=True)
class ScreenRegionRef:
    """A claim's source region; coordinates may be unavailable to text parsers."""

    label: str
    start_line: int | None = None
    end_line: int | None = None
    start_column: int | None = None
    end_column: int | None = None


@dataclass(frozen=True, slots=True)
class EvidenceDiagnostics:
    parser_name: str
    messages: tuple[str, ...] = ()
    unrecognized_regions: tuple[ScreenRegionRef, ...] = ()
    contradictory_fields: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    evidence_id: EvidenceId
    frame_id: FrameId
    source_regions: tuple[ScreenRegionRef, ...] = ()


@dataclass(frozen=True, slots=True)
class EvidenceEnvelope:
    evidence_id: EvidenceId
    frame_id: FrameId
    harness_id: HarnessId
    parser_version: str
    captured_at: datetime
    evidence_type: str
    payload: dict[str, Any]
    source_regions: tuple[ScreenRegionRef, ...] = ()
    diagnostics: EvidenceDiagnostics = field(
        default_factory=lambda: EvidenceDiagnostics(parser_name="unknown")
    )

    def ref(self) -> EvidenceRef:
        return EvidenceRef(self.evidence_id, self.frame_id, self.source_regions)
