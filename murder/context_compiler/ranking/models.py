"""Frozen proposal records for Step 4 ranking output.

These are not a ``ContextBrief``. Scores order the list for Step 5; reasons
explain enough for debugging. Exclusion detail stays in traces, not here.
"""

from __future__ import annotations

from dataclasses import dataclass

from murder.context_compiler.models import EvidenceCategory, LineRange, RecipientProfile


@dataclass(frozen=True, slots=True)
class RangeProposal:
    """One exact source range proposed for grading."""

    path: str
    line_range: LineRange
    unit_version_id: int | None
    category: EvidenceCategory
    score: float
    reasons: tuple[str, ...]
    estimated_tokens: int


@dataclass(frozen=True, slots=True)
class CorpusProposal:
    """Bounded, ranked corpus proposal for Step 5.

    ``ranges`` are in rank order. ``truncated`` is true when a hard ceiling
    stopped inclusion of further surviving candidates.
    """

    snapshot_id: int
    profile: RecipientProfile
    ranges: tuple[RangeProposal, ...]
    estimated_tokens: int
    truncated: bool


__all__ = [
    "CorpusProposal",
    "RangeProposal",
]
