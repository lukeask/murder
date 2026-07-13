# ruff: noqa: PLR0911
"""Pure verified usage collection; adapters lower ``RequestUsage`` physically."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import Enum, auto
from uuid import uuid4

from murder.llm.harness_control.model.actions import DismissOverlay, DuplicatePolicy, RequestUsage
from murder.llm.harness_control.model.observations import (
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


class UsagePhase(Enum):
    CREATED = auto()
    ENSURING_SURFACE = auto()
    REQUEST_EMITTED = auto()
    AWAITING_FRESH_USAGE = auto()
    WAITING_TO_RETRY_STALE = auto()
    RESTORING_SURFACE = auto()
    SUCCEEDED = auto()
    ESCALATED = auto()


@dataclass(frozen=True, slots=True)
class UsageRequest:
    deadline: timedelta
    require_current: bool = False
    preferred_source: str | None = None
    # Initial request plus this bounded number of distinct, journalled
    # attempts.  A stale advisory is a policy-authorized new request, never a
    # replay of an ambiguous action.
    maximum_attempts: int = 2
    stale_retry_delay: timedelta = timedelta(seconds=2)


@dataclass(frozen=True, slots=True)
class UsageOperation:
    envelope: OperationEnvelope[UsagePhase]
    request: UsageRequest
    baseline_revision: ObservationRevision | None = None
    request_action_id: str | None = None
    restoration_action_id: str | None = None
    restoration_baseline_revision: ObservationRevision | None = None
    prior_surface: SurfaceKind | None = None
    request_attempt: int = 0
    last_advisory: str | None = None
    retry_not_before: datetime | None = None


def reconcile_usage(  # noqa: PLR0912 -- explicit verified operation phases
    op: UsageOperation, snapshot: ObservationSnapshot, now: datetime
) -> ControllerDecision:
    if op.envelope.deadline and now > op.envelope.deadline:
        return ControllerDecision(
            ControllerDecisionKind.ESCALATE, UsagePhase.ESCALATED, None, "usage deadline elapsed"
        )
    usage = snapshot.usage
    freshness = _freshness_value(usage.value.freshness) if usage.value is not None else "unknown"
    fresh = (
        usage.knowledge is Knowledge.PRESENT
        and usage.value is not None
        and (not op.request.require_current or freshness == "current")
    )
    if fresh and op.request_action_id is None:
        return ControllerDecision(
            ControllerDecisionKind.SUCCEED,
            UsagePhase.SUCCEEDED,
            None,
            "fresh visible usage already satisfies request",
        )
    if op.envelope.phase in {UsagePhase.CREATED, UsagePhase.ENSURING_SURFACE}:
        if snapshot.surface.knowledge is not Knowledge.PRESENT or snapshot.surface.value is None:
            return ControllerDecision(
                ControllerDecisionKind.OBSERVE_MORE,
                UsagePhase.ENSURING_SURFACE,
                None,
                "surface unknown",
            )
        if snapshot.surface.value.primary not in {SurfaceKind.COMPOSER, SurfaceKind.TRANSCRIPT}:
            return ControllerDecision(
                ControllerDecisionKind.ESCALATE,
                UsagePhase.ESCALATED,
                None,
                "usage probe would disturb an unknown prior surface",
            )
        action = RequestUsage(
            str(uuid4()),
            op.envelope.operation_id,
            DuplicatePolicy.REPLAY_SAFE_WHILE_PRECONDITION_HOLDS,
            op.request.preferred_source,
        )
        return ControllerDecision(
            ControllerDecisionKind.EMIT_ACTION,
            UsagePhase.REQUEST_EMITTED,
            action,
            "request fresh usage",
        )
    if op.envelope.phase is UsagePhase.REQUEST_EMITTED:
        if fresh:
            return _restore_after_fresh_usage(op, snapshot)
        return ControllerDecision(
            ControllerDecisionKind.OBSERVE_MORE,
            UsagePhase.AWAITING_FRESH_USAGE,
            None,
            "await post-request observation",
        )
    if op.envelope.phase is UsagePhase.AWAITING_FRESH_USAGE:
        if op.baseline_revision is None or snapshot.revision <= op.baseline_revision:
            return ControllerDecision(
                ControllerDecisionKind.OBSERVE_MORE,
                UsagePhase.AWAITING_FRESH_USAGE,
                None,
                "usage evidence predates request",
            )
        if fresh:
            return _restore_after_fresh_usage(op, snapshot)
        if (
            op.request.require_current
            and freshness == "advisory_stale"
            and op.request_attempt < op.request.maximum_attempts
        ):
            return ControllerDecision(
                ControllerDecisionKind.OBSERVE_MORE,
                UsagePhase.WAITING_TO_RETRY_STALE,
                None,
                "Codex reported stale limits; wait before one bounded retry",
            )
        return ControllerDecision(
            ControllerDecisionKind.ESCALATE,
            UsagePhase.ESCALATED,
            None,
            "usage request emitted without fresh usable evidence",
        )
    if op.envelope.phase is UsagePhase.WAITING_TO_RETRY_STALE:
        if op.retry_not_before is not None and now < op.retry_not_before:
            return ControllerDecision(
                ControllerDecisionKind.OBSERVE_MORE,
                UsagePhase.WAITING_TO_RETRY_STALE,
                None,
                "waiting for stale usage retry interval",
            )
        if op.request_attempt >= op.request.maximum_attempts:
            return ControllerDecision(
                ControllerDecisionKind.ESCALATE,
                UsagePhase.ESCALATED,
                None,
                "stale usage retry budget exhausted",
            )
        return ControllerDecision(
            ControllerDecisionKind.EMIT_ACTION,
            UsagePhase.REQUEST_EMITTED,
            RequestUsage(
                str(uuid4()), op.envelope.operation_id,
                DuplicatePolicy.REPLAY_SAFE_WHILE_PRECONDITION_HOLDS,
                op.request.preferred_source,
            ),
            "retry stale usage with a distinct request",
        )
    if op.envelope.phase is UsagePhase.RESTORING_SURFACE:
        if (
            op.restoration_baseline_revision is None
            or snapshot.revision <= op.restoration_baseline_revision
        ):
            return ControllerDecision(
                ControllerDecisionKind.OBSERVE_MORE,
                UsagePhase.RESTORING_SURFACE,
                None,
                "wait for fresh post-dismissal surface evidence",
            )
        if _safe_surface(snapshot):
            return ControllerDecision(
                ControllerDecisionKind.SUCCEED,
                UsagePhase.SUCCEEDED,
                None,
                "usage collection restored an actionable composer surface",
            )
        return ControllerDecision(
            ControllerDecisionKind.ESCALATE,
            UsagePhase.ESCALATED,
            None,
            "usage surface did not restore to a safe composer",
        )
    return ControllerDecision(
        ControllerDecisionKind.ESCALATE, UsagePhase.ESCALATED, None, "invalid usage phase"
    )


def _safe_surface(snapshot: ObservationSnapshot) -> bool:
    if snapshot.surface.knowledge is not Knowledge.PRESENT or snapshot.surface.value is None:
        return False
    return snapshot.surface.value.primary in {SurfaceKind.COMPOSER, SurfaceKind.TRANSCRIPT}


def _freshness_value(value: object) -> str:
    """Normalize the temporary legacy spellings at the control boundary."""
    raw = getattr(value, "value", value)
    normalized = str(raw).strip().lower()
    return {
        "current": "current",
        "harness_advisory_stale": "advisory_stale",
        "advisory_stale": "advisory_stale",
    }.get(normalized, "unknown")


def _restore_after_fresh_usage(
    op: UsageOperation, snapshot: ObservationSnapshot
) -> ControllerDecision:
    if _safe_surface(snapshot):
        return ControllerDecision(
            ControllerDecisionKind.SUCCEED,
            UsagePhase.SUCCEEDED,
            None,
            "fresh usage observed and composer surface already restored",
        )
    if op.restoration_action_id is not None:
        return ControllerDecision(
            ControllerDecisionKind.ESCALATE,
            UsagePhase.ESCALATED,
            None,
            "usage overlay dismissal already emitted without safe-surface convergence",
        )
    return ControllerDecision(
        ControllerDecisionKind.EMIT_ACTION,
        UsagePhase.RESTORING_SURFACE,
        DismissOverlay(
            str(uuid4()),
            op.envelope.operation_id,
            DuplicatePolicy.REPLAY_SAFE_WHILE_PRECONDITION_HOLDS,
            snapshot.surface.value.primary.name
            if snapshot.surface.knowledge is Knowledge.PRESENT and snapshot.surface.value
            else None,
        ),
        "dismiss usage surface before reporting collection success",
    )


def advance_usage(
    op: UsageOperation, decision: ControllerDecision, snapshot: ObservationSnapshot, now: datetime
) -> UsageOperation:
    action = decision.action
    restoration_action = action if isinstance(action, DismissOverlay) else None
    status = (
        OperationStatus.SUCCEEDED
        if decision.kind is ControllerDecisionKind.SUCCEED
        else OperationStatus.ESCALATED
        if decision.kind is ControllerDecisionKind.ESCALATE
        else OperationStatus.FAILED
        if decision.kind is ControllerDecisionKind.FAIL
        else OperationStatus.RUNNING
    )
    return replace(
        op,
        envelope=replace(
            op.envelope,
            phase=decision.next_phase,
            status=status,
            updated_at=now,
            last_observation_revision=snapshot.revision,
            action_history=(*op.envelope.action_history, action.action_id)
            if action is not None
            else op.envelope.action_history,
        ),
        baseline_revision=snapshot.revision
        if isinstance(action, RequestUsage)
        else op.baseline_revision,
        request_action_id=action.action_id
        if isinstance(action, RequestUsage)
        else op.request_action_id,
        request_attempt=op.request_attempt + 1
        if isinstance(action, RequestUsage)
        else op.request_attempt,
        last_advisory=(
            snapshot.usage.value.advisory_text
            if decision.next_phase is UsagePhase.WAITING_TO_RETRY_STALE
            and snapshot.usage.knowledge is Knowledge.PRESENT
            and snapshot.usage.value is not None
            else op.last_advisory
        ),
        retry_not_before=(
            now + op.request.stale_retry_delay
            if decision.next_phase is UsagePhase.WAITING_TO_RETRY_STALE
            else None
            if isinstance(action, RequestUsage)
            else op.retry_not_before
        ),
        restoration_action_id=restoration_action.action_id
        if restoration_action is not None
        else op.restoration_action_id,
        restoration_baseline_revision=snapshot.revision
        if restoration_action is not None
        else op.restoration_baseline_revision,
        prior_surface=op.prior_surface
        or (
            snapshot.surface.value.primary
            if snapshot.surface.knowledge is Knowledge.PRESENT and snapshot.surface.value
            else None
        ),
    )
