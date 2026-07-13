"""The deep, narrow adapter boundary used by shared controllers."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from murder.llm.harness_control.model.actions import SemanticAction, TerminalEffect
from murder.llm.harness_control.model.evidence import EvidenceEnvelope, TerminalFrame
from murder.llm.harness_control.model.observations import ObservationDelta, ObservationSnapshot


class HarnessObservationAdapter(Protocol):
    parser_version: str

    def parse_evidence(
        self, frame: TerminalFrame, history: Sequence[EvidenceEnvelope]
    ) -> Sequence[EvidenceEnvelope]: ...

    def project_observations(
        self, evidence: Sequence[EvidenceEnvelope], prior: ObservationSnapshot | None
    ) -> ObservationDelta: ...


class HarnessActionAdapter(Protocol):
    def lower(
        self, action: SemanticAction, snapshot: ObservationSnapshot
    ) -> Sequence[TerminalEffect]: ...
