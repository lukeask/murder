"""Evidence-ledger retention and focused-diff caps for Step 6.

One policy module: numbers are named constants with comments. They are not
configuration knobs. Ledger lifetime follows the session, not the two-snapshot
index retention rule.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Focused changed-evidence diffs
# ---------------------------------------------------------------------------

# Unified-diff context lines on each side of a change hunk.
FOCUSED_DIFF_CONTEXT_LINES = 3

# Hard ceiling on focused-diff payload characters. Oversized diffs truncate
# with an explicit marker rather than falling back to a whole-file diff.
MAX_FOCUSED_DIFF_CHARS = 8_000

# Marker appended when a focused diff is truncated to ``MAX_FOCUSED_DIFF_CHARS``.
FOCUSED_DIFF_TRUNCATION_MARKER = "\n... [focused diff truncated]\n"

# ---------------------------------------------------------------------------
# Blob / excerpt bounds
# ---------------------------------------------------------------------------

# Refuse to store a single excerpt larger than this many characters. Excerpts
# are bounded ranges that were actually sent — never whole files.
MAX_EVIDENCE_BLOB_CHARS = 64_000

# ---------------------------------------------------------------------------
# Delivery identifiers
# ---------------------------------------------------------------------------

# Prefix for generated delivery IDs (uuid4 hex appended).
DELIVERY_ID_PREFIX = "deliv-"


__all__ = [
    "DELIVERY_ID_PREFIX",
    "FOCUSED_DIFF_CONTEXT_LINES",
    "FOCUSED_DIFF_TRUNCATION_MARKER",
    "MAX_EVIDENCE_BLOB_CHARS",
    "MAX_FOCUSED_DIFF_CHARS",
]
