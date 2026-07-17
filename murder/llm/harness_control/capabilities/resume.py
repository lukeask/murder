"""Verified opening of a harness session-resume picker."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import Enum, auto
from uuid import NAMESPACE_URL, uuid5

from murder.llm.harness_control.model.actions import (
    ConfigureResumePicker,
    DuplicatePolicy,
    OpenResumePicker,
)
from murder.llm.harness_control.model.observations import (
    ComposerActionability,
    Knowledge,
    ObservationRevision,
    ObservationSnapshot,
    SurfaceKind,
)
from murder.llm.harness_control.model.operations import (
    ControllerDecision,
    ControllerDecisionKind,
    OperationEnvelope,
    OperationStatus,
)


class OpenResumePhase(Enum):
    CREATED = auto()
    AWAITING_SAFE_SURFACE = auto()
    OPEN_EMITTED = auto()
    AWAITING_PICKER = auto()
    SUCCEEDED = auto()
    FAILED = auto()
    ESCALATED = auto()


@dataclass(frozen=True, slots=True)
class OpenResumeRequest:
    deadline: timedelta


@dataclass(frozen=True, slots=True)
class OpenResumeOperation:
    envelope: OperationEnvelope[OpenResumePhase]
    request: OpenResumeRequest
    baseline_revision: ObservationRevision | None = None
    action_id: str | None = None


@dataclass(frozen=True, slots=True)
class ResumePickerTarget:
    search_text: str = ""
    filter_mode: str = "cwd"
    sort_mode: str = "updated"

    def __post_init__(self) -> None:
        if self.filter_mode not in {"cwd", "all"}:
            raise ValueError("resume filter must be 'cwd' or 'all'")
        if self.sort_mode not in {"updated", "created"}:
            raise ValueError("resume sort must be 'updated' or 'created'")


class ConfigureResumePhase(Enum):
    CREATED = auto()
    AWAITING_PICKER = auto()
    READY = auto()
    CONFIGURATION_EMITTED = auto()
    AWAITING_READBACK = auto()
    SUCCEEDED = auto()
    FAILED = auto()
    ESCALATED = auto()


@dataclass(frozen=True, slots=True)
class ConfigureResumeOperation:
    envelope: OperationEnvelope[ConfigureResumePhase]
    target: ResumePickerTarget
    baseline_revision: ObservationRevision | None = None
    action_id: str | None = None

    @property
    def request(self) -> ResumePickerTarget:
        return self.target


def reconcile_open_resume(  # noqa: PLR0911 - explicit phase outcomes
    op: OpenResumeOperation, snapshot: ObservationSnapshot, now: datetime
) -> ControllerDecision:
    if op.envelope.deadline is not None and now >= op.envelope.deadline:
        return ControllerDecision(
            ControllerDecisionKind.FAIL,
            OpenResumePhase.FAILED,
            None,
            "resume-picker deadline exceeded",
        )
    if (
        snapshot.surface.knowledge is Knowledge.PRESENT
        and snapshot.surface.value is not None
        and snapshot.surface.value.primary is SurfaceKind.RESUME_PICKER
    ):
        return ControllerDecision(
            ControllerDecisionKind.SUCCEED,
            OpenResumePhase.SUCCEEDED,
            None,
            "resume picker is visible",
        )
    if op.envelope.phase is OpenResumePhase.CREATED:
        return ControllerDecision(
            ControllerDecisionKind.OBSERVE_MORE,
            OpenResumePhase.AWAITING_SAFE_SURFACE,
            None,
            "observe a safe surface before opening resume picker",
        )
    if op.envelope.phase is OpenResumePhase.AWAITING_SAFE_SURFACE:
        composer = snapshot.composer
        if (
            composer.knowledge is Knowledge.PRESENT
            and composer.value is not None
            and composer.value.actionability is ComposerActionability.ACTIONABLE
        ):
            action_id = str(uuid5(NAMESPACE_URL, f"{op.envelope.operation_id}:open-resume"))
            return ControllerDecision(
                ControllerDecisionKind.EMIT_ACTION,
                OpenResumePhase.OPEN_EMITTED,
                OpenResumePicker(
                    action_id,
                    op.envelope.operation_id,
                    DuplicatePolicy.REPLAY_SAFE_WHILE_PRECONDITION_HOLDS,
                ),
                "open resume picker from verified composer",
            )
        return ControllerDecision(
            ControllerDecisionKind.OBSERVE_MORE,
            OpenResumePhase.AWAITING_SAFE_SURFACE,
            None,
            "await actionable composer",
        )
    if op.baseline_revision is None or snapshot.revision <= op.baseline_revision:
        return ControllerDecision(
            ControllerDecisionKind.OBSERVE_MORE,
            OpenResumePhase.AWAITING_PICKER,
            None,
            "await fresh resume-picker evidence",
        )
    return ControllerDecision(
        ControllerDecisionKind.OBSERVE_MORE,
        OpenResumePhase.AWAITING_PICKER,
        None,
        "resume picker has not appeared yet",
    )


def advance_open_resume(
    op: OpenResumeOperation,
    decision: ControllerDecision,
    snapshot: ObservationSnapshot,
    now: datetime,
) -> OpenResumeOperation:
    phase = (
        decision.next_phase
        if isinstance(decision.next_phase, OpenResumePhase)
        else op.envelope.phase
    )
    status = (
        OperationStatus.SUCCEEDED
        if decision.kind is ControllerDecisionKind.SUCCEED
        else OperationStatus.FAILED
        if decision.kind is ControllerDecisionKind.FAIL
        else OperationStatus.ESCALATED
        if decision.kind is ControllerDecisionKind.ESCALATE
        else OperationStatus.RUNNING
    )
    action_id = op.action_id
    baseline = op.baseline_revision
    history = op.envelope.action_history
    if decision.action is not None:
        action_id = decision.action.action_id
        baseline = snapshot.revision
        history = (*history, action_id)
    return replace(
        op,
        envelope=replace(
            op.envelope,
            phase=phase,
            status=status,
            updated_at=now,
            last_observation_revision=snapshot.revision,
            action_history=history,
        ),
        baseline_revision=baseline,
        action_id=action_id,
    )


def _resume_target_matches(
    target: ResumePickerTarget, snapshot: ObservationSnapshot
) -> bool:
    if (
        snapshot.surface.knowledge is not Knowledge.PRESENT
        or snapshot.surface.value is None
        or snapshot.surface.value.primary is not SurfaceKind.RESUME_PICKER
        or snapshot.question.knowledge is not Knowledge.PRESENT
        or snapshot.question.value is None
    ):
        return False
    question = snapshot.question.value
    selected = question.active_tab or ""
    return (
        (question.custom_answer_text or "") == target.search_text
        and f"filter={target.filter_mode}" in selected.casefold()
        and f"sort={target.sort_mode}" in selected.casefold()
        and "loading=true" not in selected.casefold()
    )


def reconcile_configure_resume(  # noqa: PLR0911 - explicit phase outcomes
    op: ConfigureResumeOperation, snapshot: ObservationSnapshot, now: datetime
) -> ControllerDecision:
    if op.envelope.deadline is not None and now >= op.envelope.deadline:
        phase = (
            ConfigureResumePhase.ESCALATED
            if op.action_id is not None
            else ConfigureResumePhase.FAILED
        )
        kind = (
            ControllerDecisionKind.ESCALATE
            if op.action_id is not None
            else ControllerDecisionKind.FAIL
        )
        return ControllerDecision(kind, phase, None, "resume configuration deadline exceeded")
    if _resume_target_matches(op.target, snapshot):
        return ControllerDecision(
            ControllerDecisionKind.SUCCEED,
            ConfigureResumePhase.SUCCEEDED,
            None,
            "resume picker configuration verified",
        )
    phase = op.envelope.phase
    if phase is ConfigureResumePhase.CREATED:
        return ControllerDecision(
            ControllerDecisionKind.OBSERVE_MORE,
            ConfigureResumePhase.AWAITING_PICKER,
            None,
            "observe freshly opened resume picker",
        )
    picker_visible = (
        snapshot.surface.knowledge is Knowledge.PRESENT
        and snapshot.surface.value is not None
        and snapshot.surface.value.primary is SurfaceKind.RESUME_PICKER
        and snapshot.question.knowledge is Knowledge.PRESENT
        and snapshot.question.value is not None
        and snapshot.question.value.prompt_text == "Resume a previous session"
    )
    if phase is ConfigureResumePhase.AWAITING_PICKER:
        return ControllerDecision(
            ControllerDecisionKind.OBSERVE_MORE,
            ConfigureResumePhase.READY if picker_visible else phase,
            None,
            "fresh resume picker observed" if picker_visible else "await resume picker",
        )
    if phase is ConfigureResumePhase.READY:
        if not picker_visible:
            return ControllerDecision(
                ControllerDecisionKind.ESCALATE,
                ConfigureResumePhase.ESCALATED,
                None,
                "resume picker disappeared before configuration",
            )
        action_id = str(
            uuid5(NAMESPACE_URL, f"{op.envelope.operation_id}:configure-resume")
        )
        return ControllerDecision(
            ControllerDecisionKind.EMIT_ACTION,
            ConfigureResumePhase.CONFIGURATION_EMITTED,
            ConfigureResumePicker(
                action_id,
                op.envelope.operation_id,
                DuplicatePolicy.NEVER_AUTOMATICALLY_REPLAY,
                op.target.search_text,
                op.target.filter_mode,
                op.target.sort_mode,
            ),
            "configure freshly opened resume picker",
        )
    if op.baseline_revision is None or snapshot.revision <= op.baseline_revision:
        return ControllerDecision(
            ControllerDecisionKind.OBSERVE_MORE,
            ConfigureResumePhase.AWAITING_READBACK,
            None,
            "await fresh resume configuration readback",
        )
    if not picker_visible:
        return ControllerDecision(
            ControllerDecisionKind.ESCALATE,
            ConfigureResumePhase.ESCALATED,
            None,
            "resume picker disappeared after configuration emission",
        )
    return ControllerDecision(
        ControllerDecisionKind.OBSERVE_MORE,
        ConfigureResumePhase.AWAITING_READBACK,
        None,
        "resume picker has not reached requested configuration",
    )


def advance_configure_resume(
    op: ConfigureResumeOperation,
    decision: ControllerDecision,
    snapshot: ObservationSnapshot,
    now: datetime,
) -> ConfigureResumeOperation:
    phase = (
        decision.next_phase
        if isinstance(decision.next_phase, ConfigureResumePhase)
        else op.envelope.phase
    )
    status = (
        OperationStatus.SUCCEEDED
        if decision.kind is ControllerDecisionKind.SUCCEED
        else OperationStatus.FAILED
        if decision.kind is ControllerDecisionKind.FAIL
        else OperationStatus.ESCALATED
        if decision.kind is ControllerDecisionKind.ESCALATE
        else OperationStatus.RUNNING
    )
    action_id = op.action_id
    baseline = op.baseline_revision
    history = op.envelope.action_history
    if decision.action is not None:
        action_id = decision.action.action_id
        baseline = snapshot.revision
        history = (*history, action_id)
    return replace(
        op,
        envelope=replace(
            op.envelope,
            phase=phase,
            status=status,
            updated_at=now,
            last_observation_revision=snapshot.revision,
            action_history=history,
        ),
        baseline_revision=baseline,
        action_id=action_id,
    )
__all__ = [
    "ConfigureResumeOperation",
    "ConfigureResumePhase",
    "OpenResumeOperation",
    "OpenResumePhase",
    "OpenResumeRequest",
    "ResumePickerTarget",
    "advance_configure_resume",
    "advance_open_resume",
    "reconcile_configure_resume",
    "reconcile_open_resume",
]
