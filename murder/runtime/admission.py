"""Pure admission policy, independent from routing and execution."""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated, Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import AwareDatetime, Field

from murder.work.activities.runtime import ActivityRecord
from murder.work.workflows.runtime import WorkflowContract


class AdmissionContext(WorkflowContract):
    activity: ActivityRecord
    running_total: int = Field(ge=0)
    max_running: int = Field(ge=1)
    running_by_harness: dict[str, int] = Field(default_factory=dict)
    harness_limits: dict[str, int] = Field(default_factory=dict)
    quota_available: dict[str, bool] = Field(default_factory=dict)
    held_locks: frozenset[str] = frozenset()
    required_locks: frozenset[str] = frozenset()
    older_ready_count: int = Field(default=0, ge=0)
    now: AwareDatetime
    reservation_lease_seconds: int = Field(default=60, ge=1)
    repository: str = Field(min_length=1)
    worktree: str | None = None
    queued_at: AwareDatetime


class Admitted(WorkflowContract):
    type: Literal["admitted"] = "admitted"
    reservation_id: UUID
    reasons: tuple[str, ...]
    reservation_keys: tuple[str, ...] = ()
    admitted_at: AwareDatetime
    reservation_expires_at: AwareDatetime


class Deferred(WorkflowContract):
    type: Literal["deferred"] = "deferred"
    reasons: tuple[str, ...]
    retry_at: AwareDatetime | None = None


class Rejected(WorkflowContract):
    type: Literal["rejected"] = "rejected"
    reasons: tuple[str, ...]


AdmissionDecision = Annotated[Admitted | Deferred | Rejected, Field(discriminator="type")]


def decide_admission(context: AdmissionContext) -> AdmissionDecision:  # noqa: PLR0911
    """Decide when routed work may run; never select where it runs."""

    route = context.activity.route
    if route is None:
        return Rejected(reasons=("activity is not routed",))
    if context.running_total >= context.max_running:
        return Deferred(reasons=("global concurrency limit",))
    harness = route.assignments[0].harness
    harness_limit = context.harness_limits.get(harness)
    if (
        harness_limit is not None
        and context.running_by_harness.get(harness, 0) >= harness_limit
    ):
        return Deferred(reasons=("harness concurrency limit",))
    if context.quota_available.get(harness, True) is False:
        return Deferred(reasons=("provider quota unavailable",))
    conflicts = context.required_locks.intersection(context.held_locks)
    if conflicts:
        return Deferred(reasons=(f"locks held: {sorted(conflicts)!r}",))
    if context.older_ready_count and context.activity.priority <= 0:
        return Deferred(reasons=("older equal-priority work is waiting",))
    return Admitted(
        reservation_id=uuid5(
            NAMESPACE_URL,
            f"activity-reservation:{context.activity.activity_id}:{context.now.isoformat()}",
        ),
        reasons=("capacity and quota available",),
        reservation_keys=tuple(sorted(context.required_locks)),
        admitted_at=context.now,
        reservation_expires_at=context.now
        + timedelta(seconds=context.reservation_lease_seconds),
    )


__all__ = [
    "AdmissionContext",
    "AdmissionDecision",
    "Admitted",
    "Deferred",
    "Rejected",
    "decide_admission",
]
