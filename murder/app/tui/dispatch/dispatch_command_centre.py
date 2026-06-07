"""DispatchView — composes the ticket roster, mode strip, gauges, and calendar."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical

from murder.app.service.client_api import ScheduleSnapshot, UsageGaugeDrillInSnapshot
from murder.app.tui.components import StoreComponent
from murder.app.tui.dispatch.calendar import CalendarPanel
from murder.app.tui.dispatch.gauges import GaugeStrip
from murder.app.tui.dispatch.mode_strip import ModeStrip
from murder.app.tui.dispatch.roster import ScheduleTicketsTable

UsageDrillInLoader = Callable[..., Awaitable[UsageGaugeDrillInSnapshot]]


class DispatchView(StoreComponent, Vertical):
    """Command-centre: mode strip, ticket roster, usage, gauges, and calendar.

    StoreComponent binding: bind_stores(schedule=schedule_store)
    Bound by DefaultLayout before compose; self-subscribes on mount and cascades
    the ScheduleStoreSnapshot (duck-type compatible with ScheduleSnapshot) to
    each child widget.  Children (ModeStrip, GaugeStrip, ScheduleTicketsTable,
    CalendarPanel) use the parent-cascade pattern — they are NOT independently
    bound and render on demand from this widget's refresh_from_snapshot call.
    """

    DEFAULT_CSS = """
    DispatchView {
        height: 1fr;
    }
    DispatchView #dispatch_body {
        height: 1fr;
    }
    DispatchView #dispatch_body {
        min-width: 72;
    }
    DispatchView #schedule_tickets {
        width: 2fr;
        min-width: 72;
        height: 100%;
    }
    DispatchView CalendarPanel {
        width: 1fr;
        height: 100%;
        margin-left: 1;
    }
    DispatchView #field_deps,
    DispatchView #field_writes,
    DispatchView #field_checklist {
        height: 4;
        min-height: 3;
    }
    """

    def __init__(self) -> None:
        Vertical.__init__(self)

    def on_mount(self) -> None:
        super().on_mount()  # StoreComponent subscribes if bound

    def compose(self) -> ComposeResult:
        yield ModeStrip()
        yield GaugeStrip()
        with Horizontal(id="dispatch_body"):
            yield ScheduleTicketsTable()
            yield CalendarPanel()

    def refresh_from_snapshot(
        self,
        snapshot: Any,
        *,
        usage_drill_in_loader: UsageDrillInLoader | None = None,
    ) -> None:
        """Cascade snapshot to all child widgets.

        Accepts both ScheduleSnapshot (bridge) and ScheduleStoreSnapshot
        (self-subscribe). Children are already duck-type compatible with both.
        """
        self.query_one(ModeStrip).refresh_from_snapshot(snapshot)
        gauges = self.query_one(GaugeStrip)
        if usage_drill_in_loader is not None:
            gauges.set_drill_in_loader(usage_drill_in_loader)
        gauges.refresh_from_snapshot(snapshot)
        self.query_one(ScheduleTicketsTable).refresh_from_snapshot(snapshot)
        self.query_one(CalendarPanel).refresh_from_snapshot(snapshot)

    @property
    def selected_ticket_id(self) -> str | None:
        return self.query_one(ScheduleTicketsTable).cursor_ticket_id
