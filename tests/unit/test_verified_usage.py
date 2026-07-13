from dataclasses import replace
from datetime import datetime, timedelta, timezone

from murder.llm.harness_control.capabilities.usage import (
    UsageOperation,
    UsagePhase,
    UsageRequest,
    advance_usage,
    reconcile_usage,
)
from murder.llm.harness_control.model.evidence import HarnessId
from murder.llm.harness_control.model.observations import (
    ObservationRevision,
    Observed,
    SurfaceKind,
    SurfaceState,
    UsageState,
    unknown_snapshot,
)
from murder.llm.harness_control.model.operations import OperationEnvelope, OperationStatus

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _op(phase=UsagePhase.CREATED):
    return UsageOperation(
        OperationEnvelope(
            "u", "usage", OperationStatus.RUNNING, phase, NOW, NOW, NOW + timedelta(minutes=1)
        ),
        UsageRequest(timedelta(minutes=1)),
    )


def _snap():
    s = unknown_snapshot(HarnessId("codex"), captured_at=NOW, revision=ObservationRevision(0, 1, 1))
    r = s.revision
    return replace(
        s,
        surface=Observed.present(
            SurfaceState(
                SurfaceKind.COMPOSER,
                frozenset({SurfaceKind.COMPOSER}),
                SurfaceKind.COMPOSER,
                False,
                False,
            ),
            evidence=(),
            observed_at=NOW,
            revision=r,
        ),
    )


def test_usage_does_not_request_when_current_evidence_exists():
    s = _snap()
    s = replace(
        s,
        usage=Observed.present(
            UsageState(None, None, (), "CURRENT", None, None),
            evidence=(),
            observed_at=NOW,
            revision=s.revision,
        ),
    )
    operation = _op()
    decision = reconcile_usage(operation, s, NOW)
    assert decision.kind.name == "SUCCEED"
    assert advance_usage(operation, decision, s, NOW).envelope.status is OperationStatus.SUCCEEDED


def test_usage_requires_fresh_post_request_evidence():
    s = _snap()
    op = replace(_op(UsagePhase.AWAITING_FRESH_USAGE), baseline_revision=s.revision)
    assert reconcile_usage(op, s, NOW).kind.name == "OBSERVE_MORE"


def test_current_usage_retries_one_explicit_stale_advisory_with_a_new_action_id():
    operation = replace(_op(), request=UsageRequest(timedelta(minutes=1), require_current=True))
    request = reconcile_usage(operation, _snap(), NOW)
    operation = advance_usage(operation, request, _snap(), NOW)
    stale_snapshot = replace(
        _snap(),
        revision=ObservationRevision(0, 1, 2),
        usage=Observed.present(
            UsageState(None, None, (), "advisory_stale", None, "limits may be stale"),
            evidence=(), observed_at=NOW, revision=ObservationRevision(0, 1, 2),
        ),
    )
    awaiting = reconcile_usage(operation, stale_snapshot, NOW)
    operation = advance_usage(operation, awaiting, stale_snapshot, NOW)
    waiting = reconcile_usage(operation, stale_snapshot, NOW)
    operation = advance_usage(operation, waiting, stale_snapshot, NOW)
    assert operation.envelope.phase is UsagePhase.WAITING_TO_RETRY_STALE
    retry = reconcile_usage(operation, stale_snapshot, NOW + timedelta(seconds=2))
    assert retry.kind.name == "EMIT_ACTION"
    assert retry.action is not None
    assert retry.action.action_id != request.action.action_id


def test_diagnostic_usage_accepts_stale_evidence_without_retry():
    snapshot = replace(
        _snap(),
        usage=Observed.present(
            UsageState(None, None, (), "advisory_stale", None, "limits may be stale"),
            evidence=(), observed_at=NOW, revision=_snap().revision,
        ),
    )
    assert reconcile_usage(_op(), snapshot, NOW).kind.name == "SUCCEED"
