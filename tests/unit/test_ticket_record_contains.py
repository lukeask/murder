from __future__ import annotations

import murder.state.persistence.tickets as _tickets  # noqa: F401 — break records ↔ tickets cycle

from murder.state.persistence.records import (
    ChecklistItemRecord,
    CommandRecord,
    EscalationRecord,
    TicketRecord,
)
from murder.work.tickets.status import TicketStatus


def test_checklist_item_record_contains() -> None:
    record = ChecklistItemRecord(id=0, ord=0, text="item", done=False)
    assert "text" in record
    assert "nonexistent_field_xyz" not in record
    assert 0 not in record


def test_ticket_record_contains() -> None:
    record = TicketRecord(
        id="t001",
        title="dummy",
        status=TicketStatus.READY,
        harness=None,
        model=None,
        attempts=0,
        created_at="",
        updated_at="",
        deps=(),
        skills=(),
        checklist=(),
    )
    assert "schedule_at" in record
    assert "nonexistent_field_xyz" not in record
    assert 0 not in record


def test_command_record_contains() -> None:
    record = CommandRecord(
        id="",
        created_at="",
        updated_at="",
        run_id="",
        agent_id=None,
        role=None,
        ticket_id=None,
        target_worker="",
        kind="",
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
    assert "status" in record
    assert "nonexistent_field_xyz" not in record
    assert 0 not in record


def test_escalation_record_contains() -> None:
    record = EscalationRecord(
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
    assert "reason" in record
    assert "nonexistent_field_xyz" not in record
    assert 0 not in record
