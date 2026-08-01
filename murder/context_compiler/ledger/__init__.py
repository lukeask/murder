"""Step 6 agent-local evidence ledger: persistence, diffs, and delivery planning.

Import ``plan_evidence`` from ``murder.context_compiler.ledger.plan`` (not this
package root) when calling from persistence-adjacent code — the root only
re-exports cycle-safe symbols.
"""

from __future__ import annotations

from murder.context_compiler.ledger.diff import FocusedDiffResult, build_focused_diff
from murder.context_compiler.ledger.policy import (
    DELIVERY_ID_PREFIX,
    FOCUSED_DIFF_CONTEXT_LINES,
    FOCUSED_DIFF_TRUNCATION_MARKER,
    MAX_EVIDENCE_BLOB_CHARS,
    MAX_FOCUSED_DIFF_CHARS,
)

__all__ = [
    "DELIVERY_ID_PREFIX",
    "FOCUSED_DIFF_CONTEXT_LINES",
    "FOCUSED_DIFF_TRUNCATION_MARKER",
    "FocusedDiffResult",
    "MAX_EVIDENCE_BLOB_CHARS",
    "MAX_FOCUSED_DIFF_CHARS",
    "build_focused_diff",
]
