"""``propose_corpus`` — deterministic ranking and range shaping (Step 4).

Two-argument interface over scoring, expansion, and shaping. Callers bind
dependencies at construction time and never need to know those phases exist.

Prefer::

    propose_corpus = bind_propose_corpus(conn, worktree_root=...)
    proposal = await propose_corpus(request, snapshot)

or the equivalent ``CorpusProposer.propose_corpus`` method.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from murder.context_compiler.candidates.composite import build_default_composite
from murder.context_compiler.candidates.models import Candidate, SnapshotRef
from murder.context_compiler.candidates.protocols import CandidateProvider
from murder.context_compiler.models import ContextRequest, EvidenceLedgerEntry, LineRange
from murder.context_compiler.ranges import RangeValidationError, clamp_range
from murder.context_compiler.ranking.expansion import RelationshipExpander
from murder.context_compiler.ranking.models import CorpusProposal, RangeProposal
from murder.context_compiler.ranking.policy import DEFAULT_RANKING_POLICY, RankingPolicy
from murder.context_compiler.ranking.scoring import (
    ScoredCandidate,
    merge_candidate_maps,
    range_proposal_sort_key,
    ranking_identity,
    score_candidate,
    scored_sort_key,
)
from murder.context_compiler.ranking.shaping import RangeShaper
from murder.context_compiler.ranking.tokens import DEFAULT_TOKEN_COUNTER, TokenCounter
from murder.context_compiler.ranking.trace import RankingTrace
from murder.context_compiler.rendering import RenderError, extract_source_slice
from murder.context_compiler.source import (
    FilesystemSourceReader,
    SourceReadError,
    count_source_lines,
)

ProposeCorpusFn = Callable[..., Awaitable[CorpusProposal]]


@dataclass
class CorpusProposer:
    """Bound dependencies for ``propose_corpus(request, snapshot)``.

    Construction-time wiring only — the public operation stays two arguments.
    """

    conn: sqlite3.Connection
    worktree_root: Path
    policy: RankingPolicy = field(default_factory=lambda: DEFAULT_RANKING_POLICY)
    candidate_provider: CandidateProvider | None = None
    token_counter: TokenCounter = field(default_factory=lambda: DEFAULT_TOKEN_COUNTER)
    _last_trace: RankingTrace | None = field(default=None, init=False, repr=False)

    @property
    def last_trace(self) -> RankingTrace | None:
        return self._last_trace

    async def propose_corpus(
        self,
        request: ContextRequest,
        snapshot: SnapshotRef,
        *,
        prior_evidence: Sequence[EvidenceLedgerEntry] = (),
        trace: RankingTrace | None = None,
    ) -> CorpusProposal:
        """Rank, expand, and shape candidates into a bounded corpus proposal."""
        active_trace = trace if trace is not None else RankingTrace()
        self._last_trace = active_trace
        weights = self.policy.for_profile(request.recipient_profile)

        provider = self.candidate_provider or build_default_composite(
            self.conn,
            worktree_root=self.worktree_root,
            max_per_provider=max(20, weights.max_raw_candidates // 4),
            max_total=weights.max_raw_candidates,
        )
        raw = await provider.generate(request, snapshot, prior_evidence)
        raw_list = list(raw)[: weights.max_raw_candidates]

        # Remerge with ranking identity so distinct kinds/ranges survive.
        merged: dict[tuple[Any, ...], Candidate] = {}
        for candidate in raw_list:
            merge_candidate_maps(merged, candidate)

        reader = FilesystemSourceReader(self.worktree_root)
        text_cache: dict[str, str] = {}
        shaper = RangeShaper(
            self.conn,
            source_reader=reader,
            token_counter=self.token_counter,
        )

        # Score with exact-text token estimates when the range text is readable;
        # fall back to the line-span heuristic only when text is unavailable.
        scored_seeds: list[ScoredCandidate] = []
        for candidate in merged.values():
            est = estimate_candidate_tokens(
                candidate,
                reader=reader,
                token_counter=self.token_counter,
                text_cache=text_cache,
            )
            scored = score_candidate(candidate, weights, hop=0, estimated_tokens=est)
            scored_seeds.append(scored)
            active_trace.record(
                "scored",
                "seed",
                path=candidate.path,
                detail=",".join(scored.reasons[:6]),
                score=scored.score,
            )

        scored_seeds.sort(key=scored_sort_key)

        expander = RelationshipExpander(self.conn)
        expansion = expander.expand(
            snapshot=snapshot,
            profile=request.recipient_profile,
            weights=weights,
            seeds=tuple(scored_seeds),
            preferred_kinds=frozenset(request.relationship_kind_hints),
        )

        # Fold expansions into the candidate map.
        for candidate in expansion.candidates:
            merge_candidate_maps(merged, candidate)

        hop_by_identity = dict(expansion.hops)
        scored_all: list[ScoredCandidate] = []
        for candidate in merged.values():
            identity = ranking_identity(candidate)
            hop = hop_by_identity.get(identity, 0)
            est = estimate_candidate_tokens(
                candidate,
                reader=reader,
                token_counter=self.token_counter,
                text_cache=text_cache,
            )
            scored = score_candidate(candidate, weights, hop=hop, estimated_tokens=est)
            scored_all.append(scored)
            if hop > 0:
                active_trace.record(
                    "scored",
                    f"expanded_hop_{hop}",
                    path=candidate.path,
                    score=scored.score,
                )

        scored_all.sort(key=scored_sort_key)

        token_ceiling = weights.max_estimated_tokens
        if request.max_tokens is not None:
            token_ceiling = min(token_ceiling, request.max_tokens)

        ranges, total_tokens, truncated = shaper.shape(
            tuple(scored_all),
            snapshot=snapshot,
            weights=weights,
            token_ceiling=token_ceiling,
            trace=active_trace,
        )

        # Final deterministic order — same policy as scored_sort_key.
        ordered = tuple(sorted(ranges, key=range_proposal_sort_key))

        # Recompute total after sort (order change only).
        total_tokens = sum(r.estimated_tokens for r in ordered)
        if total_tokens > token_ceiling:
            # Trim from the end until under ceiling.
            kept: list[RangeProposal] = []
            running = 0
            for proposal in ordered:
                if running + proposal.estimated_tokens > token_ceiling:
                    truncated = True
                    active_trace.record(
                        "excluded",
                        "token_ceiling_trim",
                        path=proposal.path,
                        score=proposal.score,
                    )
                    continue
                kept.append(proposal)
                running += proposal.estimated_tokens
            ordered = tuple(kept)
            total_tokens = running

        return CorpusProposal(
            snapshot_id=snapshot.snapshot_id,
            profile=request.recipient_profile,
            ranges=ordered,
            estimated_tokens=total_tokens,
            truncated=truncated,
        )


def estimate_candidate_tokens(
    candidate: Candidate,
    *,
    reader: FilesystemSourceReader,
    token_counter: TokenCounter,
    text_cache: dict[str, str],
) -> int:
    """Exact-text token estimate when range text is available; else line heuristic.

    Pre-shape scoring uses this so token penalties reflect real excerpt cost
    without waiting for the shaper. The line-span fallback is intentional only
    when the worktree read fails or lines are unknown (file-level candidates).
    """
    if candidate.start_line is None or candidate.end_line is None:
        return _rough_token_estimate(candidate)
    try:
        if candidate.path not in text_cache:
            text_cache[candidate.path] = reader.read(candidate.path).text
        text = text_cache[candidate.path]
        line_count = max(1, count_source_lines(text))
        lr = clamp_range(LineRange(candidate.start_line, candidate.end_line), line_count)
        slice_text = extract_source_slice(text, lr.start_line, lr.end_line)
        return token_counter.count_tokens(slice_text)
    except (SourceReadError, RangeValidationError, RenderError, OSError, ValueError):
        return _rough_token_estimate(candidate)


def _rough_token_estimate(candidate: Candidate) -> int:
    """Line-span fallback when exact text is unavailable pre-shape."""
    if candidate.start_line is not None and candidate.end_line is not None:
        lines = candidate.end_line - candidate.start_line + 1
        # ~40 chars/line → /4 tokens ≈ 10 tokens/line.
        return max(0, lines * 10)
    return 0


def build_corpus_proposer(
    conn: sqlite3.Connection,
    *,
    worktree_root: Path | str,
    policy: RankingPolicy | None = None,
    candidate_provider: CandidateProvider | None = None,
    token_counter: TokenCounter | None = None,
) -> CorpusProposer:
    """Factory for a proposer bound to an experimental context-index connection."""
    return CorpusProposer(
        conn=conn,
        worktree_root=Path(worktree_root),
        policy=policy or DEFAULT_RANKING_POLICY,
        candidate_provider=candidate_provider,
        token_counter=token_counter or DEFAULT_TOKEN_COUNTER,
    )


def bind_propose_corpus(
    conn: sqlite3.Connection,
    *,
    worktree_root: Path | str,
    policy: RankingPolicy | None = None,
    candidate_provider: CandidateProvider | None = None,
    token_counter: TokenCounter | None = None,
) -> ProposeCorpusFn:
    """Return a two-arg ``propose_corpus(request, snapshot)`` with deps closed over.

    Callers use the bound function without knowing scoring / expansion / shaping
    phases exist. Optional ``prior_evidence`` / ``trace`` remain keyword-only.
    """
    proposer = build_corpus_proposer(
        conn,
        worktree_root=worktree_root,
        policy=policy,
        candidate_provider=candidate_provider,
        token_counter=token_counter,
    )

    async def propose_corpus(
        request: ContextRequest,
        snapshot: SnapshotRef,
        *,
        prior_evidence: Sequence[EvidenceLedgerEntry] = (),
        trace: RankingTrace | None = None,
    ) -> CorpusProposal:
        return await proposer.propose_corpus(
            request,
            snapshot,
            prior_evidence=prior_evidence,
            trace=trace,
        )

    return propose_corpus


__all__ = [
    "CorpusProposer",
    "ProposeCorpusFn",
    "bind_propose_corpus",
    "build_corpus_proposer",
    "estimate_candidate_tokens",
]
