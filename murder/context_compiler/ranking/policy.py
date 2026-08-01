"""Ranking thresholds, weights, caps, and budgets for Step 4.

One policy module: numbers are named constants with comments. They are not
configuration knobs. Per-profile weights live as data on ``RankingPolicy`` —
no global mutable state.
"""

from __future__ import annotations

from dataclasses import dataclass

from murder.context_compiler.models import RecipientProfile

# ---------------------------------------------------------------------------
# Shared shaping / path heuristics
# ---------------------------------------------------------------------------

# Lexical match outside a unit: window of 5 lines before and 10 after.
LEXICAL_CONTEXT_BEFORE = 5
LEXICAL_CONTEXT_AFTER = 10

# Merge nearby lexical windows when the gap between them is at most this many
# lines and the merged span stays under the merge cap.
LEXICAL_WINDOW_MERGE_GAP = 8
LEXICAL_WINDOW_MERGE_MAX_LINES = 40

# Call-site fallback window when the containing unit does not fit the unit cap.
CALL_SITE_CONTEXT_BEFORE = 3
CALL_SITE_CONTEXT_AFTER = 5

# Whole-file proposals are allowed only below this line count, and only when
# the file is relevant throughout (exact path / strong agreement).
SMALL_FILE_LINE_THRESHOLD = 40

# Unit ranges larger than this (estimated tokens) are oversized: keep them but
# prefer a focused sub-range when a match or call site is known.
DEFAULT_UNIT_TOKEN_CAP = 800

# Pre-shape oversized heuristic: units longer than this many lines get the
# large-unit penalty even before exact token estimates exist.
LARGE_UNIT_LINE_THRESHOLD = 200

# Planning second-hop gate: require max_hops at least this value.
SECOND_HOP_MIN = 2

# Path parts that mark generated or vendored source (penalty, not hard drop).
VENDORED_PATH_PARTS = frozenset(
    {
        "node_modules",
        "vendor",
        "vendors",
        "third_party",
        "third-party",
        "dist",
        "build",
        "generated",
        "__generated__",
        ".next",
        "Pods",
    }
)
GENERATED_SUFFIXES = (
    ".min.js",
    ".min.css",
    ".map",
    ".lock",
    "-lock.json",
)

# Relationship kinds eligible for expansion (outgoing and inverse as needed).
EXPAND_RELATION_KINDS = frozenset(
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

# Planning also walks these inverse / consumer edges.
PLANNING_EXTRA_INCOMING = frozenset(
    {
        "imports",  # importers
        "implements",  # implementers
        "calls",  # callers of exported API
        "references",
        "renders_component",
    }
)

# Filename-heuristic resolution methods rejected at expansion time.
FILENAME_HEURISTIC_METHODS = frozenset(
    {
        "test_filename_heuristic",
        "filename_heuristic",
        "filename_stem",
        "stem_heuristic",
    }
)

# Seed floor: only candidates at or above this scored value expand.
# Keeps weak lexical noise from fan-out.
DEFAULT_SEED_SCORE_FLOOR = 35.0

# EvidenceCategory sort priority (lower sorts earlier when scores tie).
CATEGORY_SORT_PRIORITY: dict[str, int] = {
    "edit_target": 0,
    "current_diff": 1,
    "contract": 2,
    "test": 3,
    "verification": 4,
    "supporting_context": 5,
    "other": 6,
}


@dataclass(frozen=True, slots=True)
class ProfileWeights:
    """Per-profile signal weights, penalties, hop budget, and hard ceilings."""

    # Signal weights (strongest signals should dominate when present).
    exact_hint: float
    active_diff: float
    strong_lexical: float
    direct_structural: float
    focused_test: float
    provider_agreement: float
    weak_lexical: float

    # Penalties (subtracted).
    weak_tier: float
    relationship_distance: float  # per hop beyond zero
    token_cost: float  # scaled against estimated tokens / 100
    large_unit: float
    generated_vendored: float

    # Expansion
    max_hops: int
    # Second hop only for planning, and only under this expansion count.
    second_hop_expansion_cap: int

    # Hard ceilings (not targets).
    max_raw_candidates: int
    max_expansions: int
    max_range_proposals: int
    max_estimated_tokens: int
    max_candidates_per_file: int

    seed_score_floor: float
    unit_token_cap: int
    # Steepness multiplier applied to token_cost for compact profiles.
    token_penalty_scale: float


# Compact: owner + minimum contract; steep token penalty; 0–1 hop.
_COMPACT = ProfileWeights(
    exact_hint=100.0,
    active_diff=70.0,
    strong_lexical=40.0,
    direct_structural=45.0,
    focused_test=25.0,
    provider_agreement=12.0,
    weak_lexical=5.0,
    weak_tier=25.0,
    relationship_distance=18.0,
    token_cost=8.0,  # steep: ~8 points per 100 tokens
    large_unit=30.0,
    generated_vendored=40.0,
    max_hops=1,
    second_hop_expansion_cap=0,
    max_raw_candidates=40,
    max_expansions=12,
    max_range_proposals=8,
    max_estimated_tokens=1200,
    max_candidates_per_file=2,
    seed_score_floor=50.0,
    unit_token_cap=400,
    token_penalty_scale=2.0,
)

# Implementation: edit targets, diff, contracts, callers/callees, tests,
# templates/styles. One hop. Task relevance must beat repo-global importance.
_IMPLEMENTATION = ProfileWeights(
    exact_hint=100.0,
    active_diff=85.0,
    strong_lexical=55.0,
    direct_structural=60.0,
    focused_test=70.0,
    provider_agreement=15.0,
    weak_lexical=12.0,
    weak_tier=20.0,
    relationship_distance=12.0,
    token_cost=3.0,
    large_unit=20.0,
    generated_vendored=35.0,
    max_hops=1,
    second_hop_expansion_cap=0,
    max_raw_candidates=80,
    max_expansions=40,
    max_range_proposals=24,
    max_estimated_tokens=6000,
    max_candidates_per_file=4,
    seed_score_floor=DEFAULT_SEED_SCORE_FLOOR,
    unit_token_cap=DEFAULT_UNIT_TOKEN_CAP,
    token_penalty_scale=1.0,
)

# Planning: + subsystem ownership, public contracts, consumers, config.
# One hop default; second hop under a hard expansion cap.
_PLANNING = ProfileWeights(
    exact_hint=90.0,
    active_diff=60.0,
    strong_lexical=45.0,
    direct_structural=70.0,
    focused_test=40.0,
    provider_agreement=18.0,
    weak_lexical=15.0,
    weak_tier=15.0,
    relationship_distance=8.0,
    token_cost=2.0,
    large_unit=15.0,
    generated_vendored=30.0,
    max_hops=2,
    second_hop_expansion_cap=16,
    max_raw_candidates=120,
    max_expansions=80,
    max_range_proposals=40,
    max_estimated_tokens=12000,
    max_candidates_per_file=6,
    seed_score_floor=30.0,
    unit_token_cap=1000,
    token_penalty_scale=0.8,
)


@dataclass(frozen=True, slots=True)
class RankingPolicy:
    """Per-profile weight tables as data. Construct once; pass explicitly."""

    compact: ProfileWeights = _COMPACT
    implementation: ProfileWeights = _IMPLEMENTATION
    planning: ProfileWeights = _PLANNING

    def for_profile(self, profile: RecipientProfile) -> ProfileWeights:
        if profile is RecipientProfile.COMPACT:
            return self.compact
        if profile is RecipientProfile.PLANNING:
            return self.planning
        if profile is RecipientProfile.IMPLEMENTATION:
            return self.implementation
        raise ValueError(f"unknown recipient profile: {profile!r}")


DEFAULT_RANKING_POLICY = RankingPolicy()


def is_generated_or_vendored(path: str) -> bool:
    """True when ``path`` looks generated or vendored (penalty signal)."""
    low = path.replace("\\", "/").lower()
    parts = frozenset(low.split("/"))
    if parts & VENDORED_PATH_PARTS:
        return True
    return any(low.endswith(suffix) for suffix in GENERATED_SUFFIXES)


__all__ = [
    "CALL_SITE_CONTEXT_AFTER",
    "CALL_SITE_CONTEXT_BEFORE",
    "CATEGORY_SORT_PRIORITY",
    "DEFAULT_RANKING_POLICY",
    "DEFAULT_SEED_SCORE_FLOOR",
    "DEFAULT_UNIT_TOKEN_CAP",
    "EXPAND_RELATION_KINDS",
    "FILENAME_HEURISTIC_METHODS",
    "GENERATED_SUFFIXES",
    "LEXICAL_CONTEXT_AFTER",
    "LEXICAL_CONTEXT_BEFORE",
    "LARGE_UNIT_LINE_THRESHOLD",
    "LEXICAL_WINDOW_MERGE_GAP",
    "LEXICAL_WINDOW_MERGE_MAX_LINES",
    "PLANNING_EXTRA_INCOMING",
    "ProfileWeights",
    "RankingPolicy",
    "SECOND_HOP_MIN",
    "SMALL_FILE_LINE_THRESHOLD",
    "VENDORED_PATH_PARTS",
    "is_generated_or_vendored",
]
