"""Transcript parsing package: core + per-harness grammar plugins.

Public API (re-exported for one-liner imports):
  TranscriptAccumulator, parse_frames  — from core
  supports_harness                     — from registry
  SEGMENT_TYPES                        — from segments
  _strip_leading_system_prompt         — from core (thin shim over _shared)
"""

from murder.llm.harnesses.transcripts.core import (
    BreachCounters,
    TranscriptAccumulator,
    _PaneScrollback,
    _strip_leading_system_prompt,
    parse_frames,
)
from murder.llm.harnesses.transcripts.registry import supports_harness, wants_ansi
from murder.llm.harnesses.transcripts.segments import SEGMENT_TYPES, Segment
from murder.llm.harnesses.transcripts._shared import _dedupe_adjacent, _segment_key

__all__ = [
    "SEGMENT_TYPES",
    "BreachCounters",
    "Segment",
    "TranscriptAccumulator",
    "parse_frames",
    "supports_harness",
    "wants_ansi",
    "_PaneScrollback",
    "_strip_leading_system_prompt",
    "_dedupe_adjacent",
    "_segment_key",
]
