"""Grading caps, rounds, and token budgets for Step 5.

One policy module: numbers are named constants with comments. They are not
configuration knobs. Model choice stays in ``murder/llm`` policy infrastructure.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Round / retry budgets
# ---------------------------------------------------------------------------

# Exactly one expansion round after the first grading pass when gaps exist.
# A second grading pass follows; the loop does not continue.
MAX_EXPANSION_ROUNDS = 1

# On malformed structured output: ``LlmContextGrader`` retries once with the
# validation error (this many extra attempts), then raises. ``CorpusGrader``
# does not stack another retry — total attempts before fallback stay at 2.
# Never block context production.
MAX_STRUCTURED_OUTPUT_RETRIES = 1

# ---------------------------------------------------------------------------
# RequestDelta list bounds (hints into existing providers — not operations)
# ---------------------------------------------------------------------------

# Additional path hints the model may request on a gap.
MAX_DELTA_PATH_HINTS = 8

# Additional symbol hints.
MAX_DELTA_SYMBOL_HINTS = 12

# Lexical search terms folded into the next retrieval pass.
MAX_DELTA_SEARCH_TERMS = 12

# Relationship kinds to prefer on the expansion re-rank (bounded vocabulary).
MAX_DELTA_RELATIONSHIP_KINDS = 8

# Unresolved questions recorded when gaps remain after the final pass.
MAX_UNRESOLVED_QUESTIONS = 8

# ---------------------------------------------------------------------------
# Preview / I/O token caps
# ---------------------------------------------------------------------------

# Ranges at or below this estimated-token size render full numbered source.
# Larger ranges get signature + bounded head/tail + match location.
PREVIEW_FULL_SOURCE_TOKEN_CAP = 200

# Head/tail line counts for oversized preview windows.
PREVIEW_HEAD_LINES = 12
PREVIEW_TAIL_LINES = 12

# Soft cap on total preview characters sent to the model (approx chars≈tokens*4).
# Keeps grading prompts inside cheap-model context windows.
MAX_GRADING_INPUT_CHARS = 48_000

# Structured-output completion budget. Deterministic temperature (0.0) elsewhere.
MAX_GRADING_OUTPUT_TOKENS = 2_048

# Feature type key for DirectLlmResolver / resolve_policy_client.
GRADING_FEATURE_TYPE = "context_grading"

# Capability required when resolving a live grading model.
GRADING_REQUIRED_CAPABILITY = "structured_output_reliable"

# Allowed relationship kinds in RequestDelta (must match provider vocabulary).
ALLOWED_RELATIONSHIP_KINDS = frozenset(
    {
        "contains",
        "calls",
        "references",
        "imports",
        "inherits",
        "implements",
        "renders_component",
        "template_of",
        "style_of",
        "tests",
        "configured_by",
    }
)


__all__ = [
    "ALLOWED_RELATIONSHIP_KINDS",
    "GRADING_FEATURE_TYPE",
    "GRADING_REQUIRED_CAPABILITY",
    "MAX_DELTA_PATH_HINTS",
    "MAX_DELTA_RELATIONSHIP_KINDS",
    "MAX_DELTA_SEARCH_TERMS",
    "MAX_DELTA_SYMBOL_HINTS",
    "MAX_EXPANSION_ROUNDS",
    "MAX_GRADING_INPUT_CHARS",
    "MAX_GRADING_OUTPUT_TOKENS",
    "MAX_STRUCTURED_OUTPUT_RETRIES",
    "MAX_UNRESOLVED_QUESTIONS",
    "PREVIEW_FULL_SOURCE_TOKEN_CAP",
    "PREVIEW_HEAD_LINES",
    "PREVIEW_TAIL_LINES",
]
