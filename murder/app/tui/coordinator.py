"""Ingestion coordinator — owns the runtime client and drives all stores.

Design-agnostic: knows stores and data, never widgets or layout. The bridge
(store → widget) is wired in app.py by subscribing callbacks to each store.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from murder.app.service.client_api import (
    CrowSnapshot,
    DispatchSnapshot,
    EscalationsSnapshot,
    NotesSnapshot,
    PlansSnapshot,
    ReportsSnapshot,
    ScheduleSnapshot,
)
from murder.app.tui.stores.base import StoreRegistry
from murder.app.tui.stores.conversations import ConversationsStore
from murder.app.tui.stores.dispatch import DispatchStore
from murder.app.tui.stores.documents import NotesStore, PlansStore, ReportsStore
from murder.app.tui.stores.escalations import EscalationsStore
from murder.app.tui.stores.roster import RosterStore
from murder.app.tui.stores.schedule import ScheduleStore


class IngestionCoordinator:
    """Owns the runtime client and drives all domain stores.

    Poll tick: fetches each service snapshot and ingests into the matching
    store. Stores diff against their previous snapshot and notify subscribers
    only on a real change — so polling every tick costs nothing downstream
    when idle.

    Conversation stream: forwards bus events to ConversationsStore.

    Pane tick: delegates to injected callables so no Textual widget is
    imported here.

    Bridge pattern: app.py subscribes callbacks to each store. When a store
    notifies, the callback reads ``last_*_snapshot`` and calls the widget's
    existing ``refresh_from_snapshot``. All widget knowledge stays in app.py.
    """

    def __init__(self, runtime: Any, registry: StoreRegistry | None = None) -> None:
        self._runtime = runtime
        drill_in_loader = getattr(runtime, "get_usage_gauge_drill_in", None)

        async def _noop_loader(_name: str) -> None:
            return None

        self.conversations = ConversationsStore()
        self.roster = RosterStore()
        self.dispatch = DispatchStore()
        self.schedule = ScheduleStore(drill_in_loader)
        self.escalations = EscalationsStore()
        self.plans = PlansStore(getattr(runtime, "get_plan_display", _noop_loader))
        self.notes = NotesStore(getattr(runtime, "get_note_display", _noop_loader))
        self.reports = ReportsStore(getattr(runtime, "get_report_display", _noop_loader))

        if registry is not None:
            registry.register("conversations", self.conversations)
            registry.register("roster", self.roster)
            registry.register("dispatch", self.dispatch)
            registry.register("schedule", self.schedule)
            registry.register("escalations", self.escalations)
            registry.register("plans", self.plans)
            registry.register("notes", self.notes)
            registry.register("reports", self.reports)

        # Latest raw service snapshots — read by bridge callbacks in app.py.
        # Updated unconditionally each poll tick before stores are notified.
        self.last_crow_snapshot: CrowSnapshot | None = None
        self.last_dispatch_snapshot: DispatchSnapshot | None = None
        self.last_schedule_snapshot: ScheduleSnapshot | None = None
        self.last_escalations_snapshot: EscalationsSnapshot | None = None
        self.last_plans_snapshot: PlansSnapshot | None = None
        self.last_notes_snapshot: NotesSnapshot | None = None
        self.last_reports_snapshot: ReportsSnapshot | None = None

    # ------------------------------------------------------------------
    # Poll tick
    # ------------------------------------------------------------------

    async def poll_tick(self) -> None:
        """Fetch all service snapshots and ingest into stores.

        Snapshots are cached before stores are notified, so bridge callbacks
        that read ``last_*_snapshot`` always see the just-fetched values.
        """
        crow = await self._runtime.get_crow_snapshot()
        dispatch = await self._runtime.get_dispatch_snapshot()
        schedule = await self._runtime.get_schedule_snapshot()
        plans = await self._runtime.get_plans_snapshot()
        notes = await self._runtime.get_notes_snapshot()
        reports = await self._runtime.get_reports_snapshot()
        escalations = await self._runtime.get_escalations()

        # Cache before ingestion so bridge callbacks see fresh values.
        self.last_crow_snapshot = crow
        self.last_dispatch_snapshot = dispatch
        self.last_schedule_snapshot = schedule
        self.last_plans_snapshot = plans
        self.last_notes_snapshot = notes
        self.last_reports_snapshot = reports
        self.last_escalations_snapshot = escalations

        # Ingest — each store notifies only when content changes.
        self.roster.ingest_snapshot(crow)
        self.dispatch.ingest_snapshot(dispatch)
        self.schedule.ingest_snapshot(schedule)
        self.plans.ingest_list(plans)
        self.notes.ingest_list(notes)
        self.reports.ingest_list(reports)
        self.escalations.ingest_snapshot(escalations)

    # ------------------------------------------------------------------
    # Conversation stream
    # ------------------------------------------------------------------

    async def bootstrap_conversations(self) -> None:
        """Load initial conversation snapshots into ConversationsStore."""
        loader = getattr(self._runtime, "get_conversations_snapshot", None)
        if loader is None:
            return
        snapshot = await loader()
        self.conversations.bootstrap(snapshot)

    async def run_conversation_stream(
        self, on_event: Callable[[str], None]
    ) -> None:
        """Subscribe to the bus and forward events to ConversationsStore.

        Calls ``on_event(conversation_id)`` for each event that modifies a
        conversation. Runs until cancelled.
        """
        subscribe = getattr(self._runtime, "subscribe_conversation_blocks", None)
        if subscribe is None:
            return
        async for event in subscribe():
            conversation_id = self.conversations.apply_event(event)
            if conversation_id is not None:
                on_event(conversation_id)

    # ------------------------------------------------------------------
    # Pane tick
    # ------------------------------------------------------------------

    async def pane_tick(
        self,
        *,
        refresh_mirror: Callable[[], Awaitable[None]],
        refresh_tails: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """Drive client-side pane capture for the mirror and crow tails."""
        await refresh_mirror()
        if refresh_tails is not None:
            await refresh_tails()
