"""Restart recovery classification for persisted semantic harness operations.

This module deliberately has no controller, actuator, or tmux dependency.  A
restart first obtains a new observation, then hands the classified candidate to
the capability reconciler; it never resumes a Python stack or replays effects.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from enum import Enum, auto

from murder.state.persistence.harness_control import RecoveryCandidate, load_recovery_candidates

from .operation_codec import OperationDecodeError, decode_operation_value, decode_semantic_operation

RecoveryDecodeError = OperationDecodeError


class RecoveryDisposition(Enum):
    REQUIRE_FRESH_OBSERVATION = auto()
    AMBIGUOUS_UNSAFE_EFFECT = auto()


@dataclass(frozen=True, slots=True)
class RecoveryPlan:
    candidate: RecoveryCandidate
    disposition: RecoveryDisposition
    reason: str

    @property
    def operation_id(self) -> str:
        return self.candidate.operation.operation_id


def classify_recovery_candidate(candidate: RecoveryCandidate) -> RecoveryPlan:
    """Classify persisted work without deciding its next semantic action."""
    if candidate.has_ambiguous_unsafe_effect:
        return RecoveryPlan(
            candidate,
            RecoveryDisposition.AMBIGUOUS_UNSAFE_EFFECT,
            "unsafe terminal effect was emitted or failed; "
            "require fresh evidence before reconciliation",
        )
    return RecoveryPlan(
        candidate,
        RecoveryDisposition.REQUIRE_FRESH_OBSERVATION,
        "operation has no unsafe emitted effect; require a new observation before reconciliation",
    )


def load_recovery_plans(
    conn: sqlite3.Connection, *, harness_id: str, session_id: str | None = None
) -> tuple[RecoveryPlan, ...]:
    """Load persisted candidates and classify them; this function performs no effects."""
    return tuple(
        classify_recovery_candidate(candidate)
        for candidate in load_recovery_candidates(
            conn, harness_id=harness_id, session_id=session_id
        )
    )


def reconstruct_persisted_operation(
    operation: object, *, actions: tuple[object, ...] | None = None
) -> object:
    """Reconstruct and cross-check one persisted semantic operation snapshot.

    Reconstruction is deliberately inert.  The caller must first obtain a
    current observation, then pass this value to its capability reconciler.
    """
    state = decode_semantic_operation(operation.operation_state)
    envelope = state.envelope
    checks = {
        "operation_id": (str(envelope.operation_id), operation.operation_id),
        "capability": (envelope.capability, operation.capability),
        "status": (envelope.status.name, operation.status),
        "phase_type": (
            f"{type(envelope.phase).__module__}.{type(envelope.phase).__qualname__}",
            operation.phase_type,
        ),
        "created_at": (envelope.created_at, operation.created_at),
        "updated_at": (envelope.updated_at, operation.updated_at),
        "deadline": (envelope.deadline, operation.deadline),
        "attempt_count": (envelope.attempt_count, operation.attempt_count),
        "last_observation_revision": (
            envelope.last_observation_revision,
            operation.last_observation_revision,
        ),
    }
    mismatches = [name for name, (decoded, stored) in checks.items() if decoded != stored]
    if mismatches:
        raise RecoveryDecodeError(
            f"operation_state: schema mismatch with operation columns: {', '.join(mismatches)}"
        )
    if decode_operation_value(operation.phase_payload, path="phase_payload") != envelope.phase:
        raise RecoveryDecodeError("operation_state: schema mismatch with phase payload")
    if decode_operation_value(operation.request, path="request") != state.request:
        raise RecoveryDecodeError("operation_state: schema mismatch with request payload")
    if decode_operation_value(operation.warnings, path="warnings") != envelope.warnings:
        raise RecoveryDecodeError("operation_state: schema mismatch with warnings column")
    if actions is not None:
        action_ids = tuple(action.action_id for action in actions)
        if tuple(str(action_id) for action_id in envelope.action_history) != action_ids:
            raise RecoveryDecodeError(
                "operation_state: action history disagrees with persisted action rows"
            )
    return state


__all__ = [
    "RecoveryDecodeError",
    "RecoveryDisposition",
    "RecoveryPlan",
    "classify_recovery_candidate",
    "load_recovery_plans",
    "reconstruct_persisted_operation",
]
