"""Verified configuration of interactive harness run mode and fast mode."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum, auto
from uuid import uuid4

from murder.llm.harness_control.model.actions import (
    ConfigureSessionSettings,
    DuplicatePolicy,
)
from murder.llm.harness_control.model.observations import (
    Knowledge,
    ObservationRevision,
    ObservationSnapshot,
)
from murder.llm.harness_control.model.operations import (
    ControllerDecision,
    ControllerDecisionKind,
    OperationEnvelope,
    OperationStatus,
)


@dataclass(frozen=True, slots=True)
class SessionSettingsTarget:
    run_mode: str | None = None
    fast_enabled: bool | None = None

    def __post_init__(self) -> None:
        if self.run_mode is None and self.fast_enabled is None:
            raise ValueError("at least one session setting must be requested")


class SessionSettingsPhase(Enum):
    CREATED = auto()
    AWAITING_READBACK = auto()
    SUCCEEDED = auto()
    FAILED = auto()
    ESCALATED = auto()


@dataclass(frozen=True, slots=True)
class ConfigureSessionSettingsOperation:
    envelope: OperationEnvelope[SessionSettingsPhase]
    target: SessionSettingsTarget
    action_id: str | None = None
    baseline_revision: ObservationRevision | None = None


def _matches(target: SessionSettingsTarget, snapshot: ObservationSnapshot) -> bool | None:
    observed = snapshot.settings
    if observed.knowledge is not Knowledge.PRESENT or observed.value is None:
        return None
    return bool(
        (target.run_mode is None or target.run_mode == observed.value.run_mode)
        and (
            target.fast_enabled is None
            or target.fast_enabled == observed.value.fast_enabled
        )
    )


def reconcile_session_settings(  # noqa: PLR0911 -- explicit verified outcomes
    operation: ConfigureSessionSettingsOperation,
    snapshot: ObservationSnapshot,
    now: datetime,
) -> ControllerDecision:
    if operation.envelope.deadline is not None and now > operation.envelope.deadline:
        return ControllerDecision(
            ControllerDecisionKind.ESCALATE,
            SessionSettingsPhase.ESCALATED,
            None,
            "session-settings deadline exceeded without verified readback",
        )
    matches = _matches(operation.target, snapshot)
    if operation.envelope.phase is SessionSettingsPhase.CREATED:
        if matches is True:
            return ControllerDecision(
                ControllerDecisionKind.SUCCEED,
                SessionSettingsPhase.SUCCEEDED,
                None,
                "requested session settings are already active",
            )
        if matches is None:
            return ControllerDecision(
                ControllerDecisionKind.OBSERVE_MORE,
                SessionSettingsPhase.CREATED,
                None,
                "wait for live session-settings chrome",
            )
        return ControllerDecision(
            ControllerDecisionKind.EMIT_ACTION,
            SessionSettingsPhase.AWAITING_READBACK,
            ConfigureSessionSettings(
                str(uuid4()),
                operation.envelope.operation_id,
                DuplicatePolicy.AMBIGUOUS_AFTER_EMISSION,
                operation.target.run_mode,
                operation.target.fast_enabled,
            ),
            "apply the requested interactive session settings",
        )
    if operation.envelope.phase is SessionSettingsPhase.AWAITING_READBACK:
        if operation.action_id is None or operation.baseline_revision is None:
            return ControllerDecision(
                ControllerDecisionKind.ESCALATE,
                SessionSettingsPhase.ESCALATED,
                None,
                "session-settings action lacks persisted identity or baseline",
            )
        if snapshot.revision <= operation.baseline_revision:
            return ControllerDecision(
                ControllerDecisionKind.OBSERVE_MORE,
                SessionSettingsPhase.AWAITING_READBACK,
                None,
                "wait for fresh settings chrome",
            )
        if matches is True:
            return ControllerDecision(
                ControllerDecisionKind.SUCCEED,
                SessionSettingsPhase.SUCCEEDED,
                None,
                "fresh chrome verifies requested session settings",
            )
        if matches is False:
            return ControllerDecision(
                ControllerDecisionKind.OBSERVE_MORE,
                SessionSettingsPhase.AWAITING_READBACK,
                None,
                "settings chrome has not repainted to the requested state yet",
            )
        return ControllerDecision(
            ControllerDecisionKind.OBSERVE_MORE,
            SessionSettingsPhase.AWAITING_READBACK,
            None,
            "await settings readback",
        )
    return ControllerDecision(
        ControllerDecisionKind.FAIL,
        SessionSettingsPhase.FAILED,
        None,
        f"invalid session-settings phase {operation.envelope.phase.name}",
    )


def advance_session_settings(
    operation: ConfigureSessionSettingsOperation,
    decision: ControllerDecision,
    snapshot: ObservationSnapshot,
    now: datetime,
) -> ConfigureSessionSettingsOperation:
    if not isinstance(decision.next_phase, SessionSettingsPhase):
        raise TypeError("session-settings decision has an invalid next phase")
    phase = decision.next_phase
    status = operation.envelope.status
    if decision.kind is ControllerDecisionKind.SUCCEED:
        status = OperationStatus.SUCCEEDED
    elif decision.kind is ControllerDecisionKind.FAIL:
        status = OperationStatus.FAILED
    elif decision.kind is ControllerDecisionKind.ESCALATE:
        status = OperationStatus.ESCALATED
    elif status is OperationStatus.PENDING:
        status = OperationStatus.RUNNING
    action_id, baseline = operation.action_id, operation.baseline_revision
    history = operation.envelope.action_history
    if decision.action is not None:
        action_id, baseline = decision.action.action_id, snapshot.revision
        history = (*history, action_id)
    return replace(
        operation,
        envelope=replace(
            operation.envelope,
            phase=phase,
            status=status,
            updated_at=now,
            last_observation_revision=snapshot.revision,
            action_history=history,
        ),
        action_id=action_id,
        baseline_revision=baseline,
    )


__all__ = [
    "ConfigureSessionSettingsOperation",
    "SessionSettingsPhase",
    "SessionSettingsTarget",
    "advance_session_settings",
    "reconcile_session_settings",
]
