"""Deterministic candidate providers for Context Assembler 2 (Parts 8–10).

Step 2 surface. Distinct from Step 0 ``ports.CandidateProvider`` /
``CandidateRecord`` — those remain unchanged.
"""

from __future__ import annotations

from murder.context_compiler.candidates.active_diff import ActiveDiffProvider
from murder.context_compiler.candidates.composite import (
    CompositeCandidateProvider,
    build_default_composite,
)
from murder.context_compiler.candidates.exact_hints import ExactHintsProvider
from murder.context_compiler.candidates.lexical import LexicalSearchProvider
from murder.context_compiler.candidates.models import (
    CANDIDATE_KIND_DIFF_PATH,
    CANDIDATE_KIND_EXACT_RANGE,
    CANDIDATE_KIND_FILE,
    CANDIDATE_KIND_RELATIONSHIP_NEIGHBOR,
    CANDIDATE_KIND_SEMANTIC_UNIT,
    CANDIDATE_KIND_TEST,
    SCORE_ACTIVE_DIFF_OVERLAP,
    SCORE_AMBIGUOUS_PATH,
    SCORE_AMBIGUOUS_SYMBOL,
    SCORE_DIRECT_LEXICAL,
    SCORE_DIRECT_STRUCTURAL,
    SCORE_EXACT_PATH,
    SCORE_EXACT_QUALIFIED_SYMBOL,
    SCORE_EXACT_UNIQUE_SYMBOL,
    SCORE_FOCUSED_TEST,
    SCORE_WEAK_TEXTUAL,
    Candidate,
    SnapshotRef,
    candidate_identity,
    merge_candidates,
    sort_key,
)
from murder.context_compiler.candidates.protocols import CandidateProvider
from murder.context_compiler.candidates.structural import StructuralNeighborProvider
from murder.context_compiler.candidates.tests import (
    TestRelationshipProvider,
    is_test_path,
    production_stem_from_test,
)

__all__ = [
    "CANDIDATE_KIND_DIFF_PATH",
    "CANDIDATE_KIND_EXACT_RANGE",
    "CANDIDATE_KIND_FILE",
    "CANDIDATE_KIND_RELATIONSHIP_NEIGHBOR",
    "CANDIDATE_KIND_SEMANTIC_UNIT",
    "CANDIDATE_KIND_TEST",
    "SCORE_ACTIVE_DIFF_OVERLAP",
    "SCORE_AMBIGUOUS_PATH",
    "SCORE_AMBIGUOUS_SYMBOL",
    "SCORE_DIRECT_LEXICAL",
    "SCORE_DIRECT_STRUCTURAL",
    "SCORE_EXACT_PATH",
    "SCORE_EXACT_QUALIFIED_SYMBOL",
    "SCORE_EXACT_UNIQUE_SYMBOL",
    "SCORE_FOCUSED_TEST",
    "SCORE_WEAK_TEXTUAL",
    "ActiveDiffProvider",
    "Candidate",
    "CandidateProvider",
    "CompositeCandidateProvider",
    "ExactHintsProvider",
    "LexicalSearchProvider",
    "SnapshotRef",
    "StructuralNeighborProvider",
    "TestRelationshipProvider",
    "build_default_composite",
    "candidate_identity",
    "is_test_path",
    "merge_candidates",
    "production_stem_from_test",
    "sort_key",
]
