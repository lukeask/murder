"""Candidate-provider protocol for snapshot-scoped retrieval (Part 8).

Distinct from Step 0 ``ports.CandidateProvider``, which returns
``CandidateRecord`` without a snapshot parameter. Do not change Step 0 ports.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from murder.context_compiler.candidates.models import Candidate, SnapshotRef
from murder.context_compiler.models import ContextRequest, EvidenceLedgerEntry


class CandidateProvider(Protocol):
    """Produce retrieval candidates for a request against an explicit snapshot."""

    async def generate(
        self,
        request: ContextRequest,
        snapshot: SnapshotRef,
        prior_evidence: Sequence[EvidenceLedgerEntry],
    ) -> Sequence[Candidate]: ...


__all__ = ["CandidateProvider"]
