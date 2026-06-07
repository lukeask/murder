"""Ingestion coordinator — owns the runtime client and drives all stores.

Design-agnostic: knows stores and data, never widgets or layout.  Components
self-subscribe to their stores via the StoreComponent mixin; app.py adds
coordination-only store callbacks (doc resyncs, chat routing) separately.

``last_crow_snapshot`` is retained because three app.py helpers need raw
CrowSnapshot.sessions (planner chat-target cycling, collaborator mirror
session lookup, and crow-session-for-ticket lookup) which are not available
from the projected RosterSnapshot.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from murder.app.service.client_api import (
    CrowSnapshot,
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

        # Raw crow snapshot retained for helpers that need CrowSnapshot.sessions
        # (planner chat-target cycling, collaborator mirror lookup, crow-session
        # for ticket).  All other raw snapshots are consumed via stores only.
        self.last_crow_snapshot: CrowSnapshot | None = None

    # ------------------------------------------------------------------
    # Poll tick
    # ------------------------------------------------------------------

    async def poll_tick(self) -> None:
        """Fetch all service snapshots and ingest into stores."""
        crow = await self._runtime.get_crow_snapshot()
        dispatch = await self._runtime.get_dispatch_snapshot()
        schedule = await self._runtime.get_schedule_snapshot()
        plans = await self._runtime.get_plans_snapshot()
        notes = await self._runtime.get_notes_snapshot()
        reports = await self._runtime.get_reports_snapshot()
        escalations = await self._runtime.get_escalations()

        # Cache raw crow snapshot for helpers that need CrowSnapshot.sessions.
        self.last_crow_snapshot = crow

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
