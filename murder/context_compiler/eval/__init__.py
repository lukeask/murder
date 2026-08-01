"""Deterministic evaluation harness for context-compiler retrieval.

No model calls. Cases are typed Python fixtures. Step 3's gate is expected-
unit recall and resolver correctness; ``run_cases()`` defaults to
``all_candidate_cases()``. Step 4 callers pass ``all_corpus_cases()`` for
ranking metrics. Step 5 callers pass ``all_graded_cases()`` for fake-grader
outcomes after ``grade_corpus``.
"""

from __future__ import annotations

from murder.context_compiler.eval.cases import (
    EvalCase,
    EvalCaseReport,
    EvalReport,
    RangeRef,
    UnitRef,
    case_identity,
)
from murder.context_compiler.eval.fixtures import (
    FIXTURES_ROOT,
    all_builtin_cases,
    all_candidate_cases,
    all_corpus_cases,
    all_graded_cases,
    materialize_fixture_repo,
)
from murder.context_compiler.eval.runner import (
    CandidateSnapshot,
    GradedSnapshot,
    RangeSnapshot,
    graded_corpus_fingerprint,
    run_case,
    run_cases,
    unit_ref_from_candidate,
    unit_ref_from_range,
)

__all__ = [
    "CandidateSnapshot",
    "EvalCase",
    "EvalCaseReport",
    "EvalReport",
    "FIXTURES_ROOT",
    "GradedSnapshot",
    "RangeRef",
    "RangeSnapshot",
    "UnitRef",
    "all_builtin_cases",
    "all_candidate_cases",
    "all_corpus_cases",
    "all_graded_cases",
    "case_identity",
    "graded_corpus_fingerprint",
    "materialize_fixture_repo",
    "run_case",
    "run_cases",
    "unit_ref_from_candidate",
    "unit_ref_from_range",
]
