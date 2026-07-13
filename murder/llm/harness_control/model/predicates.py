"""Pure three-valued predicate results."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

from murder.llm.harness_control.model.evidence import EvidenceRef
from murder.llm.harness_control.model.observations import ObservationRevision


class TruthValue(Enum):
    TRUE = auto()
    FALSE = auto()
    UNKNOWN = auto()


@dataclass(frozen=True, slots=True)
class PredicateResult:
    value: TruthValue
    predicate_id: str
    supporting_evidence: tuple[EvidenceRef, ...]
    observation_revision: ObservationRevision
    explanation: str
