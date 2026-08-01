"""Step 5 — cheap-model grading over Step 4 corpus proposals.

Public surface::

    from murder.context_compiler.grading import (
        build_corpus_grader,
        FakeContextGrader,
        GradeResult,
    )

    graded = await build_corpus_grader(fake, worktree_root=wt, conn=conn).grade_corpus(
        request, proposal, snapshot
    )

Domain records depend on none of ``murder.llm``. Reach the LLM through
``build_llm_context_grader`` / ``LlmContextGrader`` only.
"""

from __future__ import annotations

from murder.context_compiler.grading.delta import apply_request_delta, bound_delta
from murder.context_compiler.grading.errors import GraderOutputError
from murder.context_compiler.grading.fakes import (
    FakeContextGrader,
    exclude_paths_grader,
    gaps_then_adequate_grader,
    hallucinated_indices_grader,
    malformed_then_valid_grader,
    planning_broader_contracts_grader,
)
from murder.context_compiler.grading.grade import CorpusGrader, build_corpus_grader
from murder.context_compiler.grading.llm_adapter import (
    LlmContextGrader,
    build_llm_context_grader,
)
from murder.context_compiler.grading.models import (
    Grade,
    GradedCorpus,
    GradeResult,
    ReasonCode,
    RequestDelta,
)
from murder.context_compiler.grading.policy import (
    GRADING_FEATURE_TYPE,
    MAX_EXPANSION_ROUNDS,
    MAX_GRADING_OUTPUT_TOKENS,
)
from murder.context_compiler.grading.ports import ContextGrader
from murder.context_compiler.grading.preview import language_for_path, render_proposal_preview
from murder.context_compiler.grading.rubrics import rubric_for_profile
from murder.context_compiler.grading.structured import (
    parse_grade_result,
    parse_grade_result_json,
)
from murder.context_compiler.grading.trace import GradingTrace, GradingTraceEvent
from murder.context_compiler.grading.validate import (
    fallback_from_proposal,
    is_exact_hint_proposal,
    post_validate_grades,
)

__all__ = [
    "ContextGrader",
    "CorpusGrader",
    "FakeContextGrader",
    "GRADING_FEATURE_TYPE",
    "Grade",
    "GradeResult",
    "GradedCorpus",
    "GraderOutputError",
    "GradingTrace",
    "GradingTraceEvent",
    "LlmContextGrader",
    "MAX_EXPANSION_ROUNDS",
    "MAX_GRADING_OUTPUT_TOKENS",
    "ReasonCode",
    "RequestDelta",
    "apply_request_delta",
    "bound_delta",
    "build_corpus_grader",
    "build_llm_context_grader",
    "exclude_paths_grader",
    "fallback_from_proposal",
    "gaps_then_adequate_grader",
    "hallucinated_indices_grader",
    "is_exact_hint_proposal",
    "language_for_path",
    "malformed_then_valid_grader",
    "parse_grade_result",
    "parse_grade_result_json",
    "planning_broader_contracts_grader",
    "post_validate_grades",
    "render_proposal_preview",
    "rubric_for_profile",
]
