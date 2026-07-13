"""Trace tests for the pure verified permission-response reconciler."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from murder.llm.harness_control.capabilities.permissions import (
    AnswerPermissionOperation,
    AnswerPermissionPhase,
    PermissionAnswerRequest,
    PermissionDecisionKind,
    PermissionResponseTarget,
    advance_answer_permission,
    permission_fingerprint,
    reconcile_answer_permission,
)
from murder.llm.harness_control.model import (
    AnswerPermission,
    ChoiceState,
    ControllerDecisionKind,
    DuplicatePolicy,
    HarnessId,
    Knowledge,
    ObservationRevision,
    Observed,
    OperationEnvelope,
    OperationStatus,
    PermissionRequestState,
    unknown_snapshot,
)

NOW = datetime(2026, 7, 11, tzinfo=timezone.utc)


def _permission(
    *,
    risks: frozenset[str] = frozenset({"shell", "write"}),
    acknowledged_response_id: str | None = None,
) -> PermissionRequestState:
    return PermissionRequestState(
        "permission-1",
        "shell",
        "rm -rf temporary",
        "Clean generated files",
        (
            ChoiceState("allow_once", "Allow once", disabled=False, highlighted=True),
            ChoiceState("deny", "Deny", disabled=False),
        ),
        "allow_once",
        risks,
        acknowledged_response_id,
    )


def _snapshot(permission: PermissionRequestState | None, revision: ObservationRevision):
    snapshot = unknown_snapshot(HarnessId("claude_code"), captured_at=NOW, revision=revision)
    observed = (
        Observed.present(permission, evidence=(), observed_at=NOW, revision=revision)
        if permission is not None
        else Observed.without_value(
            Knowledge.ABSENT, observed_at=NOW, revision=revision, explanation="request resolved"
        )
    )
    return replace(snapshot, permission_request=observed)


def _request(permission: PermissionRequestState, *, risks: frozenset[str] | None = None):
    return PermissionAnswerRequest(
        "permission-1",
        permission_fingerprint(permission),
        PermissionResponseTarget("allow_once", "Allow once", PermissionDecisionKind.ALLOW_ONCE),
        permission.risk_attributes if risks is None else risks,
    )


def _operation(
    phase: AnswerPermissionPhase,
    permission: PermissionRequestState,
    *,
    action_id: str | None = None,
    baseline: ObservationRevision | None = None,
) -> AnswerPermissionOperation:
    envelope = OperationEnvelope(
        "permission-op",
        "answer_permission",
        OperationStatus.RUNNING,
        phase,
        NOW,
        NOW,
        NOW + timedelta(minutes=1),
    )
    return AnswerPermissionOperation(envelope, _request(permission), action_id, baseline)


def test_permission_response_is_bound_to_risk_and_never_replayed() -> None:
    permission = _permission()
    decision = reconcile_answer_permission(
        _operation(AnswerPermissionPhase.READY_TO_RESPOND, permission),
        _snapshot(permission, ObservationRevision(0, 2, 2)),
        NOW,
    )

    assert decision.kind is ControllerDecisionKind.EMIT_ACTION
    assert isinstance(decision.action, AnswerPermission)
    assert decision.action.duplicate_policy is DuplicatePolicy.NEVER_AUTOMATICALLY_REPLAY
    assert decision.action.response_id == "allow_once"


def test_risk_change_escalates_before_permission_response() -> None:
    recorded = _permission()
    visible = _permission(risks=frozenset({"shell", "write", "network"}))
    decision = reconcile_answer_permission(
        _operation(AnswerPermissionPhase.READY_TO_RESPOND, recorded),
        _snapshot(visible, ObservationRevision(0, 2, 2)),
        NOW,
    )

    assert decision.kind is ControllerDecisionKind.ESCALATE
    assert decision.action is None


def test_fresh_disappearance_is_ambiguous_not_permission_resolution() -> None:
    permission = _permission()
    decision = reconcile_answer_permission(
        _operation(
            AnswerPermissionPhase.AWAITING_ACKNOWLEDGMENT,
            permission,
            action_id="response-1",
            baseline=ObservationRevision(0, 1, 1),
        ),
        _snapshot(None, ObservationRevision(0, 2, 2)),
        NOW,
    )

    assert decision.kind is ControllerDecisionKind.OBSERVE_MORE


def test_same_permission_after_emission_escalates_without_second_approval() -> None:
    permission = _permission()
    decision = reconcile_answer_permission(
        _operation(
            AnswerPermissionPhase.AWAITING_ACKNOWLEDGMENT,
            permission,
            action_id="response-1",
            baseline=ObservationRevision(0, 1, 1),
        ),
        _snapshot(permission, ObservationRevision(0, 2, 2)),
        NOW,
    )

    assert decision.kind is ControllerDecisionKind.ESCALATE
    assert decision.action is None


def test_explicit_matching_permission_acknowledgment_verifies_resolution() -> None:
    permission = _permission(acknowledged_response_id="allow_once")
    decision = reconcile_answer_permission(
        _operation(
            AnswerPermissionPhase.AWAITING_ACKNOWLEDGMENT,
            permission,
            action_id="response-1",
            baseline=ObservationRevision(0, 1, 1),
        ),
        _snapshot(permission, ObservationRevision(0, 2, 2)),
        NOW,
    )

    assert decision.kind is ControllerDecisionKind.SUCCEED


def test_replacement_permission_is_ambiguous_without_resolution_evidence() -> None:
    permission = _permission()
    replacement = PermissionRequestState(
        "permission-2",
        "network",
        "curl https://example.invalid",
        "Fetch dependency metadata",
        (ChoiceState("allow_once", "Allow once", disabled=False, highlighted=True),),
        "allow_once",
        frozenset({"network"}),
    )
    decision = reconcile_answer_permission(
        _operation(
            AnswerPermissionPhase.AWAITING_ACKNOWLEDGMENT,
            permission,
            action_id="response-1",
            baseline=ObservationRevision(0, 1, 1),
        ),
        _snapshot(replacement, ObservationRevision(0, 2, 2)),
        NOW,
    )

    assert decision.kind is ControllerDecisionKind.OBSERVE_MORE


def test_post_response_timeout_escalates_instead_of_replaying_permission() -> None:
    permission = _permission()
    decision = reconcile_answer_permission(
        _operation(
            AnswerPermissionPhase.AWAITING_ACKNOWLEDGMENT,
            permission,
            action_id="response-1",
            baseline=ObservationRevision(0, 1, 1),
        ),
        _snapshot(permission, ObservationRevision(0, 2, 2)),
        NOW + timedelta(minutes=2),
    )

    assert decision.kind is ControllerDecisionKind.ESCALATE
    assert decision.action is None


def test_unknown_response_disabled_state_waits_instead_of_approving() -> None:
    permission = PermissionRequestState(
        "permission-1",
        "shell",
        "echo hi",
        None,
        (ChoiceState("allow_once", "Allow once", disabled=None),),
        None,
        frozenset(),
    )
    request = _request(permission)
    envelope = OperationEnvelope(
        "permission-op",
        "answer_permission",
        OperationStatus.RUNNING,
        AnswerPermissionPhase.READY_TO_RESPOND,
        NOW,
        NOW,
        NOW + timedelta(minutes=1),
    )
    decision = reconcile_answer_permission(
        AnswerPermissionOperation(envelope, request),
        _snapshot(permission, ObservationRevision(0, 2, 2)),
        NOW,
    )

    assert decision.kind is ControllerDecisionKind.OBSERVE_MORE


def test_permission_response_records_identity_and_freshness_before_emission() -> None:
    permission = _permission()
    snapshot = _snapshot(permission, ObservationRevision(0, 2, 2))
    op = _operation(AnswerPermissionPhase.READY_TO_RESPOND, permission)
    decision = reconcile_answer_permission(op, snapshot, NOW)
    advanced = advance_answer_permission(op, decision, snapshot, NOW)
    assert advanced.response_action_id == decision.action.action_id
    assert advanced.baseline_revision == snapshot.revision
