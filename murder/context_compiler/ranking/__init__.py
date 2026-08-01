"""Step 4 — deterministic ranking and range shaping.

Public surface::

    propose_corpus = bind_propose_corpus(conn, worktree_root=...)
    proposal = await propose_corpus(request, snapshot)

No model calls. Output is a bounded ``CorpusProposal`` for Step 5, not a
``ContextBrief``.
"""

from __future__ import annotations

from murder.context_compiler.ranking.models import CorpusProposal, RangeProposal
from murder.context_compiler.ranking.policy import (
    DEFAULT_RANKING_POLICY,
    ProfileWeights,
    RankingPolicy,
    is_generated_or_vendored,
)
from murder.context_compiler.ranking.propose import (
    CorpusProposer,
    ProposeCorpusFn,
    bind_propose_corpus,
    build_corpus_proposer,
)
from murder.context_compiler.ranking.scoring import (
    range_proposal_sort_key,
    ranking_identity,
    score_candidate,
)
from murder.context_compiler.ranking.tokens import (
    DEFAULT_TOKEN_COUNTER,
    ApproxTokenCounter,
    TokenCounter,
)
from murder.context_compiler.ranking.trace import RankingTrace, TraceEvent

__all__ = [
    "ApproxTokenCounter",
    "CorpusProposal",
    "CorpusProposer",
    "DEFAULT_RANKING_POLICY",
    "DEFAULT_TOKEN_COUNTER",
    "ProfileWeights",
    "ProposeCorpusFn",
    "RangeProposal",
    "RankingPolicy",
    "RankingTrace",
    "TokenCounter",
    "TraceEvent",
    "bind_propose_corpus",
    "build_corpus_proposer",
    "is_generated_or_vendored",
    "range_proposal_sort_key",
    "ranking_identity",
    "score_candidate",
]
