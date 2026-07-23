"""Tests for the Mapping-like __contains__ contract on persistence records.

All four record types expose __contains__ as part of a dict-compatible
protocol. One parametrized test pins the shared contract: field membership,
missing-field rejection, and non-string key rejection.
"""

from __future__ import annotations

import pytest

import murder.state.persistence.tickets as _tickets  # noqa: F401 — break records ↔ tickets cycle
from murder.state.persistence.records import (
    ChecklistItemRecord,
    CommandRecord,
    EscalationRecord,
    TicketRecord,
)
from murder.runtime.orchestration.worker_names import WorkerName
from murder.runtime.orchestration.commands import OrchestrationCommand
from murder.work.tickets.status import TicketStatus


def _make_checklist_item() -> ChecklistItemRecord:
    return ChecklistItemRecord(id=0, ord=0, text="item", done=False)


def _make_ticket() -> TicketRecord:
    return TicketRecord(
        id="t001",
        title="dummy",
        status=TicketStatus.READY,
        harness=None,
        model=None,
        worktree=None,
        attempts=0,
        created_at="",
        updated_at="",
        deps=(),
        checklist=(),
    )


def _make_command() -> CommandRecord:
    return CommandRecord(
        id="",
        created_at="",
        updated_at="",
        run_id="",
        agent_id=None,
        role=None,
        ticket_id=None,
        target_worker=WorkerName.ORCHESTRATOR,
        kind=OrchestrationCommand.AGENT_STOP,
        payload_json="{}",
        correlation_id="",
        idempotency_key="",
        status="pending",
        claimed_by=None,
        lease_expires_at=None,
        attempt_count=0,
        retryable=0,
        result_json=None,
        last_error=None,
    )


def _make_escalation() -> EscalationRecord:
    return EscalationRecord(
        id=0,
        ts="",
        ticket_id=None,
        severity=0,
        reason="test",
        to_recipient="",
        body_path=None,
        resolved=False,
        resolved_at=None,
        source_event_id=None,
    )


@pytest.mark.parametrize(
    "record, present_field",
    [
        (_make_checklist_item(), "text"),
        (_make_ticket(), "schedule_at"),
        (_make_command(), "status"),
        (_make_escalation(), "reason"),
    ],
)
def test_record_contains_present_field_not_missing_not_nonstring(record, present_field) -> None:
    assert present_field in record
    assert "nonexistent_field_xyz" not in record
    assert 0 not in record
