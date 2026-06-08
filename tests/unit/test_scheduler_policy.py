from __future__ import annotations

from murder.verdict.policy.scheduler_policy import (
    SchedulerCaps,
    SchedulerInput,
    SchedulerParams,
    SchedulerWindow,
    TicketRecord,
    decide,
)


def _input(ready_tickets: list[TicketRecord]) -> SchedulerInput:
    return SchedulerInput(
        window=SchedulerWindow(
            harness="codex",
            window_key="5h",
            percent_used=99.0,
            t_until_reset=1.0,
            t_period=300.0,
        ),
        params=SchedulerParams(),
        harness_busy={"codex": False},
        provider_budgets={},
        caps=SchedulerCaps(),
        ready_tickets=ready_tickets,
    )


def test_ticket_order_prefers_scheduled_then_timestamp_then_id() -> None:
    decision = decide(
        _input(
            [
                TicketRecord(id="t003", schedule_at=None, harness="codex"),
                TicketRecord(id="t002", schedule_at="2026-06-08T09:00:00", harness="codex"),
                TicketRecord(id="t001", schedule_at="2026-06-08T09:00:00", harness="codex"),
                TicketRecord(id="t004", schedule_at="2026-06-08T08:00:00", harness="codex"),
            ]
        )
    )

    assert decision.action == "kick"
    assert decision.ticket_id == "t004"


def test_unscheduled_tickets_sort_by_id_after_scheduled() -> None:
    decision = decide(
        _input(
            [
                TicketRecord(id="t003", schedule_at=None, harness="codex"),
                TicketRecord(id="t001", schedule_at=None, harness="codex"),
            ]
        )
    )

    assert decision.action == "kick"
    assert decision.ticket_id == "t001"
