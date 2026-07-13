"""Pure, verified reconciliation for permission responses.

Permission policy supplies the decision.  This capability only verifies that
the exact observed request still matches that decision, emits one unsafe
semantic response, and requires later evidence that it resolved.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum, auto
from uuid import NAMESPACE_URL, uuid5

from murder.llm.harness_control.model.actions import AnswerPermission, DuplicatePolicy
from murder.llm.harness_control.model.observations import (
    ChoiceState,
    Knowledge,
    ObservationRevision,
    ObservationSnapshot,
    PermissionRequestState,
)
from murder.llm.harness_control.model.operations import (
    ControllerDecision,
    ControllerDecisionKind,
    OperationEnvelope,
    OperationStatus,
)
from murder.llm.harness_control.model.predicates import PredicateResult, TruthValue


class PermissionDecisionKind(Enum):
    ALLOW_ONCE = auto()
    ALLOW_FOR_SESSION = auto()
    DENY = auto()
    CANCEL = auto()
    HARNESS_SPECIFIC = auto()


class AnswerPermissionPhase(Enum):
    CREATED = auto()
    AWAITING_REQUEST = auto()
    READY_TO_RESPOND = auto()
    RESPONSE_EMITTED = auto()
    AWAITING_ACKNOWLEDGMENT = auto()
    RESOLVED = auto()
    AMBIGUOUS = auto()
    FAILED = auto()
    ESCALATED = auto()


@dataclass(frozen=True, slots=True)
class PermissionResponseTarget:
    stable_response_id: str | None
    label: str
    kind: PermissionDecisionKind

    def __post_init__(self) -> None:
        if not self.stable_response_id and not self.label.strip():
            raise ValueError("a permission response needs an id or a non-empty label")


@dataclass(frozen=True, slots=True)
class PermissionAnswerRequest:
    """A policy/user decision bound to one exact permission request."""

    request_id_hint: str | None
    request_fingerprint: str | None
    response: PermissionResponseTarget
    expected_risk_attributes: frozenset[str] | None = None

    def __post_init__(self) -> None:
        if not self.request_id_hint and not self.request_fingerprint:
            raise ValueError("a permission decision requires an id hint or fingerprint")


@dataclass(frozen=True, slots=True)
class AnswerPermissionOperation:
    envelope: OperationEnvelope[AnswerPermissionPhase]
    request: PermissionAnswerRequest
    response_action_id: str | None = None
    baseline_revision: ObservationRevision | None = None
    ambiguity_reason: str | None = None


def permission_fingerprint(request: PermissionRequestState) -> str:
    choices = "\x1e".join(
        f"{choice.stable_choice_id or ''}\x1f{choice.label.strip()}" for choice in request.choices
    )
    raw = "\x1d".join(
        (
            (request.tool_name or "").strip(),
            (request.command or "").strip(),
            (request.description or "").strip(),
            choices,
        )
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _predicate(
    snapshot: ObservationSnapshot,
    name: str,
    value: TruthValue,
    reason: str,
    *,
    evidence: tuple = (),
) -> PredicateResult:
    return PredicateResult(value, name, evidence, snapshot.revision, reason)


def permission_matches(
    expected: PermissionAnswerRequest, snapshot: ObservationSnapshot
) -> PredicateResult:
    observed = snapshot.permission_request
    if observed.knowledge is not Knowledge.PRESENT or observed.value is None:
        return _predicate(
            snapshot,
            "permission_matches",
            TruthValue.UNKNOWN,
            f"permission request is {observed.knowledge.name.lower()}",
            evidence=observed.evidence,
        )
    request = observed.value
    checks: list[bool] = []
    if expected.request_id_hint:
        if request.request_id_hint is None:
            return _predicate(
                snapshot,
                "permission_matches",
                TruthValue.UNKNOWN,
                "observed permission request has no stable id",
                evidence=observed.evidence,
            )
        checks.append(request.request_id_hint == expected.request_id_hint)
    if expected.request_fingerprint:
        checks.append(permission_fingerprint(request) == expected.request_fingerprint)
    if expected.expected_risk_attributes is not None:
        checks.append(request.risk_attributes == expected.expected_risk_attributes)
    return _predicate(
        snapshot,
        "permission_matches",
        TruthValue.TRUE if all(checks) else TruthValue.FALSE,
        "permission request identity and risk binding compared",
        evidence=observed.evidence,
    )


def _find_choice(
    choices: tuple[ChoiceState, ...], target: PermissionResponseTarget
) -> ChoiceState | None | bool:
    if target.stable_response_id:
        return next(
            (choice for choice in choices if choice.stable_choice_id == target.stable_response_id),
            None,
        )
    matches = [choice for choice in choices if choice.label == target.label]
    return matches[0] if len(matches) == 1 else (None if not matches else False)


def permission_response_available(
    expected: PermissionAnswerRequest, snapshot: ObservationSnapshot
) -> PredicateResult:
    observed = snapshot.permission_request
    if observed.knowledge is not Knowledge.PRESENT or observed.value is None:
        return _predicate(
            snapshot,
            "permission_response_available",
            TruthValue.UNKNOWN,
            "permission choices are not currently known",
            evidence=observed.evidence,
        )
    choice = _find_choice(observed.value.choices, expected.response)
    if choice is False:
        return _predicate(
            snapshot,
            "permission_response_available",
            TruthValue.UNKNOWN,
            f"permission response label {expected.response.label!r} is ambiguous",
            evidence=observed.evidence,
        )
    if choice is None:
        return _predicate(
            snapshot,
            "permission_response_available",
            TruthValue.FALSE,
            f"permission response {expected.response.label!r} is unavailable",
            evidence=observed.evidence,
        )
    if choice.disabled is None:
        return _predicate(
            snapshot,
            "permission_response_available",
            TruthValue.UNKNOWN,
            "permission response disabled state is unknown",
            evidence=observed.evidence,
        )
    return _predicate(
        snapshot,
        "permission_response_available",
        TruthValue.FALSE if choice.disabled else TruthValue.TRUE,
        "permission response availability evaluated",
        evidence=observed.evidence,
    )


def permission_response_acknowledged(
    op: AnswerPermissionOperation, snapshot: ObservationSnapshot
) -> PredicateResult:
    if (
        op.response_action_id is None
        or op.baseline_revision is None
        or snapshot.revision <= op.baseline_revision
    ):
        return _predicate(
            snapshot,
            "permission_response_acknowledged",
            TruthValue.UNKNOWN,
            "no fresh observation after permission response emission",
        )
    observed = snapshot.permission_request
    if observed.knowledge is not Knowledge.PRESENT or observed.value is None:
        reason = (
            "permission request disappeared without a correlated resolution acknowledgment"
            if observed.knowledge is Knowledge.ABSENT
            else f"permission request is {observed.knowledge.name.lower()}"
        )
        return _predicate(
            snapshot,
            "permission_response_acknowledged",
            TruthValue.UNKNOWN,
            reason,
            evidence=observed.evidence,
        )
    matched = permission_matches(op.request, snapshot)
    if matched.value is TruthValue.FALSE:
        return _predicate(
            snapshot,
            "permission_response_acknowledged",
            TruthValue.UNKNOWN,
            "request changed without a correlated resolution acknowledgment",
            evidence=observed.evidence,
        )
    if matched.value is TruthValue.UNKNOWN:
        return _predicate(
            snapshot,
            "permission_response_acknowledged",
            TruthValue.UNKNOWN,
            "current permission request cannot be safely correlated",
            evidence=observed.evidence,
        )
    if (
        op.request.response.stable_response_id is not None
        and observed.value.acknowledged_response_id == op.request.response.stable_response_id
    ):
        return _predicate(
            snapshot,
            "permission_response_acknowledged",
            TruthValue.TRUE,
            "permission dialog reports the recorded response as acknowledged",
            evidence=observed.evidence,
        )
    return _predicate(
        snapshot,
        "permission_response_acknowledged",
        TruthValue.FALSE,
        "same permission request remains after unsafe response emission",
        evidence=observed.evidence,
    )


def _response_action(op: AnswerPermissionOperation) -> AnswerPermission:
    request = op.request
    action_id = str(uuid5(NAMESPACE_URL, f"{op.envelope.operation_id}:answer-permission"))
    return AnswerPermission(
        action_id,
        op.envelope.operation_id,
        DuplicatePolicy.NEVER_AUTOMATICALLY_REPLAY,
        request.request_id_hint,
        request.response.stable_response_id,
        request.response.label,
    )


def reconcile_answer_permission(  # noqa: PLR0911, PLR0912 -- typed operation phases
    op: AnswerPermissionOperation, snapshot: ObservationSnapshot, now: datetime
) -> ControllerDecision:
    if op.envelope.deadline is not None and now >= op.envelope.deadline:
        if op.envelope.phase in {
            AnswerPermissionPhase.RESPONSE_EMITTED,
            AnswerPermissionPhase.AWAITING_ACKNOWLEDGMENT,
        }:
            return ControllerDecision(
                ControllerDecisionKind.ESCALATE,
                AnswerPermissionPhase.AMBIGUOUS,
                None,
                "permission response was emitted without verified acknowledgment before deadline",
            )
        return ControllerDecision(
            ControllerDecisionKind.FAIL,
            AnswerPermissionPhase.FAILED,
            None,
            "permission response deadline exceeded before emission",
        )
    if snapshot.health.requires_escalation:
        return ControllerDecision(
            ControllerDecisionKind.ESCALATE,
            AnswerPermissionPhase.ESCALATED,
            None,
            "observation health requires escalation",
        )
    phase = op.envelope.phase
    if phase is AnswerPermissionPhase.CREATED:
        return ControllerDecision(
            ControllerDecisionKind.OBSERVE_MORE,
            AnswerPermissionPhase.AWAITING_REQUEST,
            None,
            "begin verified permission response",
        )
    if phase is AnswerPermissionPhase.AWAITING_REQUEST:
        identity = permission_matches(op.request, snapshot)
        if identity.value is TruthValue.TRUE:
            return ControllerDecision(
                ControllerDecisionKind.OBSERVE_MORE,
                AnswerPermissionPhase.READY_TO_RESPOND,
                None,
                "target permission request is visible",
                (identity,),
            )
        if identity.value is TruthValue.FALSE:
            return ControllerDecision(
                ControllerDecisionKind.ESCALATE,
                AnswerPermissionPhase.ESCALATED,
                None,
                "a different permission request is visible",
                (identity,),
            )
        return ControllerDecision(
            ControllerDecisionKind.OBSERVE_MORE,
            phase,
            None,
            "await target permission evidence",
            (identity,),
        )
    if phase is AnswerPermissionPhase.READY_TO_RESPOND:
        identity = permission_matches(op.request, snapshot)
        availability = permission_response_available(op.request, snapshot)
        predicates = (identity, availability)
        if identity.value is TruthValue.FALSE or availability.value is TruthValue.FALSE:
            return ControllerDecision(
                ControllerDecisionKind.ESCALATE,
                AnswerPermissionPhase.ESCALATED,
                None,
                "permission request or response changed",
                predicates,
            )
        if identity.value is not TruthValue.TRUE or availability.value is not TruthValue.TRUE:
            return ControllerDecision(
                ControllerDecisionKind.OBSERVE_MORE,
                phase,
                None,
                "permission-response preconditions remain uncertain",
                predicates,
            )
        if op.response_action_id is not None:
            return ControllerDecision(
                ControllerDecisionKind.ESCALATE,
                AnswerPermissionPhase.AMBIGUOUS,
                None,
                "a permission response action already exists",
                predicates,
            )
        return ControllerDecision(
            ControllerDecisionKind.EMIT_ACTION,
            AnswerPermissionPhase.RESPONSE_EMITTED,
            _response_action(op),
            "respond to verified permission request",
            predicates,
        )
    if phase in {
        AnswerPermissionPhase.RESPONSE_EMITTED,
        AnswerPermissionPhase.AWAITING_ACKNOWLEDGMENT,
    }:
        acknowledged = permission_response_acknowledged(op, snapshot)
        if acknowledged.value is TruthValue.TRUE:
            return ControllerDecision(
                ControllerDecisionKind.SUCCEED,
                AnswerPermissionPhase.RESOLVED,
                None,
                "permission response acknowledged",
                (acknowledged,),
            )
        if acknowledged.value is TruthValue.FALSE:
            return ControllerDecision(
                ControllerDecisionKind.ESCALATE,
                AnswerPermissionPhase.AMBIGUOUS,
                None,
                "permission response lacks compatible acknowledgment",
                (acknowledged,),
            )
        return ControllerDecision(
            ControllerDecisionKind.OBSERVE_MORE,
            AnswerPermissionPhase.AWAITING_ACKNOWLEDGMENT,
            None,
            "await fresh permission-response acknowledgment",
            (acknowledged,),
        )
    if phase is AnswerPermissionPhase.AMBIGUOUS:
        return ControllerDecision(
            ControllerDecisionKind.ESCALATE,
            AnswerPermissionPhase.ESCALATED,
            None,
            op.ambiguity_reason or "ambiguous permission response",
        )
    return ControllerDecision(
        ControllerDecisionKind.FAIL,
        AnswerPermissionPhase.FAILED,
        None,
        f"invalid permission phase {phase.name}",
    )


def advance_answer_permission(
    op: AnswerPermissionOperation,
    decision: ControllerDecision,
    snapshot: ObservationSnapshot,
    now: datetime,
) -> AnswerPermissionOperation:
    """Persist response intent before terminal I/O; approvals are not replayable."""

    status = _status_after(op.envelope.status, decision.kind)
    phase = (
        decision.next_phase
        if isinstance(decision.next_phase, AnswerPermissionPhase)
        else op.envelope.phase
    )
    action_history = op.envelope.action_history
    action_id = op.response_action_id
    baseline = op.baseline_revision
    if decision.action is not None:
        action_history = (*action_history, decision.action.action_id)
        action_id = decision.action.action_id
        baseline = snapshot.revision
    return replace(
        op,
        envelope=replace(
            op.envelope,
            phase=phase,
            status=status,
            updated_at=now,
            last_observation_revision=snapshot.revision,
            action_history=action_history,
        ),
        response_action_id=action_id,
        baseline_revision=baseline,
        ambiguity_reason=decision.reason
        if decision.kind is ControllerDecisionKind.ESCALATE
        else op.ambiguity_reason,
    )


def _status_after(status: OperationStatus, kind: ControllerDecisionKind) -> OperationStatus:
    if kind is ControllerDecisionKind.SUCCEED:
        return OperationStatus.SUCCEEDED
    if kind is ControllerDecisionKind.FAIL:
        return OperationStatus.FAILED
    if kind is ControllerDecisionKind.ESCALATE:
        return OperationStatus.ESCALATED
    return OperationStatus.RUNNING if status is OperationStatus.PENDING else status


__all__ = [
    "AnswerPermissionOperation",
    "AnswerPermissionPhase",
    "advance_answer_permission",
    "PermissionAnswerRequest",
    "PermissionDecisionKind",
    "PermissionResponseTarget",
    "permission_fingerprint",
    "permission_matches",
    "permission_response_acknowledged",
    "permission_response_available",
    "reconcile_answer_permission",
]
