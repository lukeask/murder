"""Pure scheduling policy for crow_magic mode (no I/O)."""

from __future__ import annotations

from dataclasses import dataclass

@dataclass(frozen=True)
class SchedulerWindow:
    harness: str
    window_key: str
    percent_used: float
    t_until_reset: float
    t_period: float


@dataclass(frozen=True)
class SchedulerParams:
    c_changeoff: float = 0.7
    t_alwaysyes: float = 15.0
    alwayscutoff: float = 0.6
    intensity: float = 1.0
    multiharness_cutoff: float | None = None


@dataclass(frozen=True)
class SchedulerCaps:
    pass


@dataclass(frozen=True)
class TicketRecord:
    id: str
    wave: int
    schedule_at: str | None
    harness: str | None


@dataclass(frozen=True)
class SchedulerInput:
    window: SchedulerWindow
    params: SchedulerParams
    harness_busy: dict[str, bool]
    provider_budgets: dict[str, float]
    caps: SchedulerCaps
    ready_tickets: list[TicketRecord]


@dataclass(frozen=True)
class SchedulerDecision:
    action: str
    ticket_id: str | None
    provider: str | None
    harness: str | None
    rationale: str
    threshold_used: float | None


def usage_reset_detected(
    prev_pct: float,
    curr_pct: float,
    *,
    prev_min: float = 30.0,
    curr_max: float = 5.0,
) -> bool:
    """True when usage dropped sharply enough to signal a provider reset window."""
    return prev_pct >= prev_min and curr_pct <= curr_max


def _ticket_sort_key(ticket: TicketRecord) -> tuple[int, int, str, str]:
    return (
        ticket.wave,
        1 if ticket.schedule_at is None else 0,
        ticket.schedule_at or "",
        ticket.id,
    )


def _pick_best_ticket(ready_tickets: list[TicketRecord], harness: str) -> TicketRecord | None:
    eligible = [t for t in ready_tickets if t.harness == harness or t.harness is None]
    if not eligible:
        return None
    return min(eligible, key=_ticket_sort_key)


def decide(input: SchedulerInput) -> SchedulerDecision:
    """Return hold/kick for one (harness, window) evaluation."""
    from murder.scheduler.usage_threshold_curve import _f_threshold

    window = input.window
    harness = window.harness
    window_key = window.window_key
    percent_used = window.percent_used
    usage = percent_used / 100.0

    threshold = _f_threshold(
        window.t_until_reset,
        window.t_period,
        c_changeoff=input.params.c_changeoff,
        t_alwaysyes=input.params.t_alwaysyes,
        alwayscutoff=input.params.alwayscutoff,
        intensity=input.params.intensity,
    )

    if usage < threshold:
        return SchedulerDecision(
            action="hold",
            ticket_id=None,
            provider=None,
            harness=harness,
            rationale=(
                f"Holding: {harness}/{window_key} usage {percent_used:.0f}%"
                f" below threshold {threshold * 100:.0f}%"
            ),
            threshold_used=threshold,
        )

    multiharness_cutoff = input.params.multiharness_cutoff
    if multiharness_cutoff is not None:
        cutoff_frac = (
            multiharness_cutoff / 100.0 if multiharness_cutoff > 1.0 else multiharness_cutoff
        )
        if usage < cutoff_frac and input.harness_busy.get(harness, False):
            return SchedulerDecision(
                action="hold",
                ticket_id=None,
                provider=None,
                harness=harness,
                rationale=(
                    f"Holding: {harness}/{window_key} usage {percent_used:.0f}%"
                    f" below multiharness cutoff {cutoff_frac * 100:.0f}% (harness busy)"
                ),
                threshold_used=threshold,
            )

    ticket = _pick_best_ticket(input.ready_tickets, harness)
    if ticket is None:
        return SchedulerDecision(
            action="hold",
            ticket_id=None,
            provider=None,
            harness=harness,
            rationale=(
                f"No ready tickets for {harness}/{window_key}"
                f" (usage {percent_used:.0f}% ≥ threshold {threshold * 100:.0f}%)"
            ),
            threshold_used=threshold,
        )

    return SchedulerDecision(
        action="kick",
        ticket_id=ticket.id,
        provider=None,
        harness=harness,
        rationale=(
            f"Kicking {ticket.id}: {harness}/{window_key} usage {percent_used:.0f}%"
            f" ≥ threshold {threshold * 100:.0f}%"
        ),
        threshold_used=threshold,
    )


__all__ = [
    "SchedulerCaps",
    "SchedulerDecision",
    "SchedulerInput",
    "SchedulerParams",
    "SchedulerWindow",
    "TicketRecord",
    "decide",
    "usage_reset_detected",
]
