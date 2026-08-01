"""Resolution precedence, confidence tiers, and caps for Step 3.

One policy module: thresholds and caps are named constants with comments.
They are not configuration. Ranking (Step 4) may later read these tiers;
nothing here invents float "probabilities".
"""

from __future__ import annotations

from murder.context_compiler.persistence.records import ConfidenceTier

# Three confidence tiers. Precedence ranks map onto these — the same fact
# expressed twice. Persist the tier; never a calibrated probability.
CONFIDENCE_EXACT: ConfidenceTier = "exact"
CONFIDENCE_INFERRED: ConfidenceTier = "inferred"
CONFIDENCE_WEAK: ConfidenceTier = "weak"

# Precedence ranks (highest first). Lower-precedence rules never override a
# higher-precedence exact match for the same target.
#
# 1 exact path or exact qualified name
# 2 imported alias resolved through a known export
# 3 unique unqualified name, repository-wide or in a strongly related file
# 4 framework selector or component tag
# 5 filename or stem heuristic
PRECEDENCE_EXACT_PATH = 1
PRECEDENCE_IMPORTED_ALIAS = 2
PRECEDENCE_UNIQUE_UNQUALIFIED = 3
PRECEDENCE_FRAMEWORK_SELECTOR = 4
PRECEDENCE_FILENAME_HEURISTIC = 5

_TIER_BY_PRECEDENCE: dict[int, ConfidenceTier] = {
    PRECEDENCE_EXACT_PATH: CONFIDENCE_EXACT,
    PRECEDENCE_IMPORTED_ALIAS: CONFIDENCE_EXACT,
    PRECEDENCE_UNIQUE_UNQUALIFIED: CONFIDENCE_INFERRED,
    PRECEDENCE_FRAMEWORK_SELECTOR: CONFIDENCE_INFERRED,
    PRECEDENCE_FILENAME_HEURISTIC: CONFIDENCE_WEAK,
}

# Cap textual / unqualified fan-out to avoid common-identifier explosions.
# Eight targets is enough to show ambiguity without flooding the index.
MAX_REFERENCE_CANDIDATES = 8
# Stop looking up unqualified names once fan-out exceeds this; the identifier
# is too common to be useful as a repository-wide signal.
MAX_UNQUALIFIED_LOOKUP = 16
# Identifiers shorter than this are almost always noise (loop vars, etc.).
MIN_IDENTIFIER_LEN = 3

# Float→tier bands used only when adapting extractor-emitted floats at the
# persistence boundary. Extractors still speak floats today; the DB does not.
# 0.85+ aligns with ranks 1–2 style certainty; below 0.60 is heuristic/weak.
_FLOAT_EXACT_MIN = 0.85
_FLOAT_INFERRED_MIN = 0.60


def tier_for_precedence(rank: int) -> ConfidenceTier:
    """Map a precedence rank (1–5) to its confidence tier."""
    try:
        return _TIER_BY_PRECEDENCE[rank]
    except KeyError as exc:
        raise ValueError(f"unknown precedence rank: {rank}") from exc


def tier_from_float(value: float) -> ConfidenceTier:
    """Adapt an extractor float confidence into a persisted tier."""
    if value >= _FLOAT_EXACT_MIN:
        return CONFIDENCE_EXACT
    if value >= _FLOAT_INFERRED_MIN:
        return CONFIDENCE_INFERRED
    return CONFIDENCE_WEAK


def tier_rank(tier: ConfidenceTier) -> int:
    """Numeric order for sorting (higher is stronger)."""
    if tier == CONFIDENCE_EXACT:
        return 3
    if tier == CONFIDENCE_INFERRED:
        return 2
    if tier == CONFIDENCE_WEAK:
        return 1
    raise ValueError(f"unknown confidence tier: {tier}")


def normalize_confidence(value: ConfidenceTier | float | str) -> ConfidenceTier:
    """Accept a tier, legacy float, or persisted string; always return a tier."""
    if isinstance(value, float):
        return tier_from_float(value)
    if value in (CONFIDENCE_EXACT, CONFIDENCE_INFERRED, CONFIDENCE_WEAK):
        return value
    raise ValueError(f"invalid confidence: {value!r}")


__all__ = [
    "CONFIDENCE_EXACT",
    "CONFIDENCE_INFERRED",
    "CONFIDENCE_WEAK",
    "ConfidenceTier",
    "MAX_REFERENCE_CANDIDATES",
    "MAX_UNQUALIFIED_LOOKUP",
    "MIN_IDENTIFIER_LEN",
    "PRECEDENCE_EXACT_PATH",
    "PRECEDENCE_FILENAME_HEURISTIC",
    "PRECEDENCE_FRAMEWORK_SELECTOR",
    "PRECEDENCE_IMPORTED_ALIAS",
    "PRECEDENCE_UNIQUE_UNQUALIFIED",
    "normalize_confidence",
    "tier_for_precedence",
    "tier_from_float",
    "tier_rank",
]
