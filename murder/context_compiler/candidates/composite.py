"""Composite candidate provider: union, dedupe, order, limits (Part 9)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from murder.context_compiler.candidates.active_diff import ActiveDiffProvider
from murder.context_compiler.candidates.exact_hints import ExactHintsProvider
from murder.context_compiler.candidates.lexical import LexicalSearchProvider
from murder.context_compiler.candidates.models import (
    Candidate,
    SnapshotRef,
    candidate_identity,
    merge_candidates,
    sort_key,
)
from murder.context_compiler.candidates.protocols import CandidateProvider
from murder.context_compiler.candidates.structural import StructuralNeighborProvider
from murder.context_compiler.candidates.tests import TestRelationshipProvider
from murder.context_compiler.models import ContextRequest, EvidenceLedgerEntry

PROVIDER_ID = "composite"


@dataclass(frozen=True, slots=True)
class CompositeCandidateProvider:
    """Union configured providers with deterministic merge and bounds.

    Does not perform final task-aptness ranking.
    """

    providers: tuple[CandidateProvider, ...]
    max_per_provider: int = 80
    max_total: int = 200
    provider_limits: Mapping[str, int] | None = None

    async def generate(
        self,
        request: ContextRequest,
        snapshot: SnapshotRef,
        prior_evidence: Sequence[EvidenceLedgerEntry],
    ) -> Sequence[Candidate]:
        merged: dict[tuple[object, ...], Candidate] = {}

        for provider in self.providers:
            provider_name = _provider_name(provider)
            limit = self._limit_for(provider_name)
            raw = await provider.generate(request, snapshot, prior_evidence)
            capped = sorted(raw, key=sort_key)[:limit]
            for candidate in capped:
                identity = candidate_identity(candidate)
                existing = merged.get(identity)
                if existing is None:
                    merged[identity] = candidate
                else:
                    merged[identity] = merge_candidates(existing, candidate)

        ordered = sorted(merged.values(), key=sort_key)
        return tuple(ordered[: self.max_total])

    def _limit_for(self, provider_name: str) -> int:
        if self.provider_limits and provider_name in self.provider_limits:
            return int(self.provider_limits[provider_name])
        return self.max_per_provider


def _provider_name(provider: CandidateProvider) -> str:
    for attr in ("provider_id", "PROVIDER_ID"):
        value = getattr(provider, attr, None)
        if isinstance(value, str) and value:
            return value
    cls = type(provider)
    for attr in ("PROVIDER_ID", "provider_id"):
        value = getattr(cls, attr, None)
        if isinstance(value, str) and value:
            return value
    module = getattr(cls, "__module__", "") or ""
    for suffix, name in (
        (".exact_hints", "exact_hints"),
        (".lexical", "lexical"),
        (".structural", "structural"),
        (".active_diff", "active_diff"),
        (".tests", "tests"),
        (".composite", "composite"),
    ):
        if module.endswith(suffix):
            return name
    return cls.__name__


def build_default_composite(
    conn: Any,
    *,
    worktree_root: Path | str | None = None,
    max_per_provider: int = 80,
    max_total: int = 200,
) -> CompositeCandidateProvider:
    """Convenience: exact → lexical → structural → diff → tests."""
    exact = ExactHintsProvider(conn)
    root = Path(worktree_root) if worktree_root is not None else None
    return CompositeCandidateProvider(
        providers=(
            exact,
            LexicalSearchProvider(conn, worktree_root=root),
            StructuralNeighborProvider(conn, seed_provider=exact),
            ActiveDiffProvider(conn, worktree_root=root),
            TestRelationshipProvider(conn, seed_provider=exact),
        ),
        max_per_provider=max_per_provider,
        max_total=max_total,
    )


__all__ = [
    "PROVIDER_ID",
    "CompositeCandidateProvider",
    "build_default_composite",
]
