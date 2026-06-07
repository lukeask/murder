"""ScheduleStore — poll-fed store for scheduler decisions and usage gauges.

Holds the full ScheduleSnapshot content (minus ``as_of``) plus lazily-loaded
usage gauge drill-in detail, cached by ``(harness, window_key)``.

Usage drill-in pattern mirrors the document body loader in documents.py:
  - ``request_drill_in(harness, window_key)`` is idempotent; second call is a
    cache hit with no loader invocation.
  - Drill-in entries are evicted when their ``(harness, window_key)`` pair is
    absent from the latest ``usage_gauges`` (covers gauge removal and window
    rotation).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from murder.app.service.client_api import (
    CalendarRunningAgent,
    CalendarScheduledTicket,
    ScheduleSnapshot,
    SchedulerDecisionSummary,
    ScheduleTicketRow,
    UsageGaugeDrillInSnapshot,
    UsageGaugeSummary,
)
from murder.app.tui.stores.base import BaseStore

UsageDrillInLoader = Callable[..., Awaitable[UsageGaugeDrillInSnapshot]]


@dataclass(frozen=True, slots=True)
class ScheduleStoreSnapshot:
    """Immutable snapshot emitted by ScheduleStore.

    ``as_of`` from the server snapshot is deliberately excluded.
    ``drill_ins`` holds the sorted cached drill-in detail snapshots.
    """

    scheduler_mode: str
    mode_rationale: str
    active_tickets: tuple[ScheduleTicketRow, ...]
    recent_done_tickets: tuple[ScheduleTicketRow, ...]
    archived_tickets: tuple[ScheduleTicketRow, ...]
    scheduler_decisions: tuple[SchedulerDecisionSummary, ...]
    usage_gauges: tuple[UsageGaugeSummary, ...]
    calendar_harnesses: tuple[str, ...]
    running_agents: tuple[CalendarRunningAgent, ...]
    scheduled_tickets: tuple[CalendarScheduledTicket, ...]
    invalidation_key: str
    drill_ins: tuple[UsageGaugeDrillInSnapshot, ...]  # sorted by (harness, window_key)


class ScheduleStore(BaseStore[ScheduleStoreSnapshot]):
    def __init__(self, loader: UsageDrillInLoader) -> None:
        super().__init__(
            ScheduleStoreSnapshot(
                scheduler_mode="",
                mode_rationale="",
                active_tickets=(),
                recent_done_tickets=(),
                archived_tickets=(),
                scheduler_decisions=(),
                usage_gauges=(),
                calendar_harnesses=(),
                running_agents=(),
                scheduled_tickets=(),
                invalidation_key="",
                drill_ins=(),
            )
        )
        self._loader = loader
        self._drill_in_cache: dict[tuple[str, str], UsageGaugeDrillInSnapshot] = {}
        self._last_server_snapshot: ScheduleSnapshot | None = None

    def ingest_snapshot(self, snapshot: ScheduleSnapshot) -> None:
        """Called by the poll tick; notifies subscribers only when content changed."""
        self._last_server_snapshot = snapshot
        # Evict cache entries whose (harness, window_key) is no longer present.
        current_keys = {(g.harness, g.window_key) for g in snapshot.usage_gauges}
        self._drill_in_cache = {
            k: v for k, v in self._drill_in_cache.items() if k in current_keys
        }
        self._set(self._build(snapshot))

    async def request_drill_in(self, harness: str, window_key: str) -> None:
        """Ensure drill-in for ``(harness, window_key)`` is loaded; no-op if cached.

        Looks up ``t_period_minutes`` from the current gauge list, calls the
        injected loader once, caches and rebuilds so subscribers see the result.
        """
        key = (harness, window_key)
        if key in self._drill_in_cache:
            return
        current = self.get_snapshot()
        gauge = next(
            (
                g
                for g in current.usage_gauges
                if g.harness == harness and g.window_key == window_key
            ),
            None,
        )
        if gauge is None or self._last_server_snapshot is None:
            return
        drill_in = await self._loader(
            harness=harness,
            window_key=window_key,
            t_period_minutes=gauge.t_period_minutes,
        )
        self._drill_in_cache[key] = drill_in
        self._set(self._build(self._last_server_snapshot))

    # -- internal ----------------------------------------------------------

    def _build(self, snapshot: ScheduleSnapshot) -> ScheduleStoreSnapshot:
        drill_ins = tuple(
            sorted(
                self._drill_in_cache.values(),
                key=lambda d: (d.harness, d.window_key),
            )
        )
        return ScheduleStoreSnapshot(
            scheduler_mode=snapshot.scheduler_mode,
            mode_rationale=snapshot.mode_rationale,
            active_tickets=snapshot.active_tickets,
            recent_done_tickets=snapshot.recent_done_tickets,
            archived_tickets=snapshot.archived_tickets,
            scheduler_decisions=snapshot.scheduler_decisions,
            usage_gauges=snapshot.usage_gauges,
            calendar_harnesses=snapshot.calendar_harnesses,
            running_agents=snapshot.running_agents,
            scheduled_tickets=snapshot.scheduled_tickets,
            invalidation_key=snapshot.invalidation_key,
            drill_ins=drill_ins,
        )
