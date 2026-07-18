"""Recoverable dispatcher composing routing, admission, claims, and execution."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol
from uuid import UUID

from murder.runtime.admission import (
    AdmissionContext,
    AdmissionDecision,
    decide_admission,
)
from murder.runtime.sessions.contracts import SessionStatus
from murder.runtime.sessions.persistence import SessionStore
from murder.state.persistence.activities import (
    ActivityLifecycleError,
    claim_activity,
    complete_activity,
    list_activities,
    persist_admission,
    persist_route,
    reap_expired_claims,
    reap_expired_reservations,
    renew_activity_claim,
    start_activity,
)
from murder.state.persistence.harness_models import get_all_harness_models
from murder.work.activities.runtime import (
    ActivityClaim,
    ActivityFailure,
    ActivityOutcome,
    ActivityRecord,
    ActivityStatus,
    ExecutionRoute,
)
from murder.work.routing import RouteCandidate, RoutingContext, decide_route

LOGGER = logging.getLogger(__name__)


class ActivityRouter(Protocol):
    def __call__(self, activity: ActivityRecord) -> ExecutionRoute | None: ...


class ActivityAdmissionProvider(Protocol):
    def __call__(self, activity: ActivityRecord) -> AdmissionDecision: ...


class ActivityExecutor(Protocol):
    def __call__(
        self,
        activity: ActivityRecord,
        claim: ActivityClaim,
        renew: Callable[[], ActivityClaim],
    ) -> Awaitable[ActivityOutcome]: ...


@dataclass(frozen=True, slots=True)
class DispatchReport:
    routed: int = 0
    admitted: int = 0
    completed: int = 0
    deferred: int = 0
    reaped_claims: int = 0
    reaped_reservations: int = 0


class ActivityDispatcher:
    """One finite tick over durable state; safe to reconstruct after restart."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        router: ActivityRouter,
        admission: ActivityAdmissionProvider,
        executor: ActivityExecutor,
        worker_id: str,
        lease_for: timedelta = timedelta(minutes=2),
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._connection = connection
        self._router = router
        self._admission = admission
        self._executor = executor
        self._worker_id = worker_id
        self._lease_for = lease_for
        self._clock = clock

    async def tick(self, *, limit: int = 100) -> DispatchReport:  # noqa: PLR0915
        if limit < 1:
            raise ValueError("limit must be positive")
        now = self._clock()
        reaped_claims = reap_expired_claims(self._connection, now=now)
        reaped_reservations = reap_expired_reservations(self._connection, now=now)
        routed = admitted = completed = deferred = 0
        runnable: list[ActivityRecord] = []
        for status in (
            ActivityStatus.PENDING,
            ActivityStatus.ROUTING,
            ActivityStatus.WAITING_ADMISSION,
        ):
            runnable.extend(list_activities(self._connection, status=status))
        runnable.sort(key=lambda item: (-item.priority, item.created_at, str(item.activity_id)))
        for candidate in runnable[:limit]:
            try:
                activity = candidate
                if activity.status in {ActivityStatus.PENDING, ActivityStatus.ROUTING}:
                    route = self._router(activity)
                    if route is None:
                        deferred += 1
                        continue
                    activity = persist_route(
                        self._connection,
                        activity.activity_id,
                        route,
                        now=now,
                    )
                    routed += 1
                decision = self._admission(activity)
                activity = persist_admission(
                    self._connection,
                    activity.activity_id,
                    decision,
                    now=now,
                )
                if activity.status == ActivityStatus.CANCELLED:
                    completed += 1
                    continue
                from murder.runtime.admission import Admitted  # noqa: PLC0415

                if not isinstance(decision, Admitted):
                    deferred += 1
                    continue
                admitted += 1
                assert activity.route is not None
                claim = claim_activity(
                    self._connection,
                    activity.activity_id,
                    owner=self._worker_id,
                    lease_for=self._lease_for,
                    capability_revision=activity.route.capability_revision,
                    now=now,
                )
                activity = start_activity(self._connection, claim, now=now)

                def renew() -> ActivityClaim:
                    nonlocal claim
                    claim = renew_activity_claim(
                        self._connection,
                        claim,
                        lease_for=self._lease_for,
                        now=self._clock(),
                    )
                    return claim

                async def heartbeat() -> None:
                    while True:
                        await asyncio.sleep(max(0.1, self._lease_for.total_seconds() / 3))
                        renew()

                heartbeat_task = asyncio.create_task(heartbeat())
                try:
                    outcome = await self._executor(activity, claim, renew)
                except Exception as exc:
                    outcome = ActivityFailure(
                        code="executor_error",
                        message=str(exc) or type(exc).__name__,
                        retryable=claim.attempt < activity.max_attempts,
                    )
                finally:
                    heartbeat_task.cancel()
                    try:
                        await heartbeat_task
                    except asyncio.CancelledError:
                        pass
                complete_activity(
                    self._connection,
                    claim,
                    outcome,
                    now=self._clock(),
                )
                completed += 1
            except ActivityLifecycleError as exc:
                if "admission resource lock is already reserved" in str(exc):
                    deferred += 1
                    continue
                LOGGER.warning(
                    "activity %s lifecycle error during tick; deferring",
                    candidate.activity_id,
                    exc_info=True,
                )
                deferred += 1
            except Exception:
                LOGGER.warning(
                    "activity %s unexpected error during tick; deferring",
                    candidate.activity_id,
                    exc_info=True,
                )
                deferred += 1
        return DispatchReport(
            routed=routed,
            admitted=admitted,
            completed=completed,
            deferred=deferred,
            reaped_claims=reaped_claims,
            reaped_reservations=reaped_reservations,
        )


def build_default_activity_dispatcher(
    connection: sqlite3.Connection,
    *,
    worker_id: str = "activity-dispatcher",
    max_running: int = 8,
) -> ActivityDispatcher:
    """Compose production routing, admission, and session-bound execution."""

    from murder.runtime.activity_executor import (  # noqa: PLC0415
        build_session_bound_executor,
    )

    sessions = SessionStore(connection)
    clock = lambda: datetime.now(timezone.utc)

    def router(activity: ActivityRecord) -> ExecutionRoute | None:
        candidates = _route_candidates(connection, sessions, activity)
        decision = decide_route(
            RoutingContext(
                activity_id=activity.activity_id,
                requirements=activity.requirements,
                candidates=candidates,
            )
        )
        return decision.route

    def admission(activity: ActivityRecord) -> AdmissionDecision:
        now = clock()
        running = list_activities(connection, status=ActivityStatus.RUNNING)
        claimed = list_activities(connection, status=ActivityStatus.CLAIMED)
        live = (*running, *claimed)
        running_by_harness: dict[str, int] = {}
        for item in live:
            if item.route is None or not item.route.assignments:
                continue
            harness = item.route.assignments[0].harness
            running_by_harness[harness] = running_by_harness.get(harness, 0) + 1
        required_locks = frozenset(
            {
                key
                for key in (
                    activity.requirements.worktree,
                    activity.requirements.max_parallelism_group,
                )
                if key
            }
        )
        held_locks = frozenset(
            {
                key
                for item in live
                for key in (
                    item.requirements.worktree,
                    item.requirements.max_parallelism_group,
                )
                if key
            }
        )
        return decide_admission(
            AdmissionContext(
                activity=activity,
                running_total=len(live),
                max_running=max_running,
                running_by_harness=running_by_harness,
                required_locks=required_locks,
                held_locks=held_locks,
                repository=activity.requirements.worktree or "default",
                worktree=activity.requirements.worktree,
                queued_at=activity.created_at,
                now=now,
            )
        )

    return ActivityDispatcher(
        connection,
        router=router,
        admission=admission,
        executor=build_session_bound_executor(connection),
        worker_id=worker_id,
        clock=clock,
    )


def _route_candidates(
    connection: sqlite3.Connection,
    sessions: SessionStore,
    activity: ActivityRecord,
) -> tuple[RouteCandidate, ...]:
    reusable: dict[str, list[UUID]] = {}
    for session in sessions.list_recoverable_sessions():
        if session.status not in {
            SessionStatus.READY,
            SessionStatus.WORKING,
            SessionStatus.AWAITING_INPUT,
        }:
            continue
        reusable.setdefault(session.harness, []).append(session.session_id)
    rows = get_all_harness_models(connection)
    if not rows:
        # Fresh databases may not have discovered models yet. Prefer declared
        # harnesses from the activity so routing can still hold or select.
        preferred = activity.requirements.preferred_harnesses or ("codex",)
        return tuple(
            RouteCandidate(
                harness=name,
                models=activity.requirements.preferred_models or ("default",),
                capability_tags=activity.requirements.capability_tags or frozenset({"coding"}),
                structured_protocol=activity.requirements.require_structured_protocol,
                terminal=True,
                reusable_session_ids=tuple(reusable.get(name, ())),
                available=True,
            )
            for name in preferred
        )
    candidates: list[RouteCandidate] = []
    for row in rows:
        harness = str(row["harness"])
        models = tuple(
            str(item["id"])
            for item in row["models"]  # type: ignore[index]
            if isinstance(item, dict) and item.get("id")
        )
        if not models:
            continue
        candidates.append(
            RouteCandidate(
                harness=harness,
                models=models,
                capability_tags=frozenset({"coding"}),
                structured_protocol=True,
                terminal=True,
                reusable_session_ids=tuple(reusable.get(harness, ())),
                available=row.get("discovery_error") is None,
            )
        )
    return tuple(candidates)
