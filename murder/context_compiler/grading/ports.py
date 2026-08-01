"""Narrow grading port — one method combining item grades and adequacy gaps."""

from __future__ import annotations

from typing import Protocol

from murder.context_compiler.grading.models import GradeResult
from murder.context_compiler.models import ContextRequest
from murder.context_compiler.ranking.models import CorpusProposal


class ContextGrader(Protocol):
    """Cheap-model judgement over a Step 4 ``CorpusProposal``.

    Adequacy is the same question as grading — answered in one call via
    ``GradeResult.gaps``. Implementations must not execute retrieval or mutate
    ranges; deterministic post-validation owns that.
    """

    async def grade(
        self,
        request: ContextRequest,
        proposal: CorpusProposal,
    ) -> GradeResult: ...


__all__ = [
    "ContextGrader",
]
