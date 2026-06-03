"""DispatchView — composes the ticket roster, mode strip, gauges, and calendar."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical

from murder.service.client_api import ScheduleSnapshot, UsageGaugeDrillInSnapshot
from murder.tui.dispatch.calendar import CalendarPanel
from murder.tui.dispatch.gauges import GaugeStrip
from murder.tui.dispatch.mode_strip import ModeStrip
from murder.tui.dispatch.roster import ScheduleTicketsTable

UsageDrillInLoader = Callable[..., Awaitable[UsageGaugeDrillInSnapshot]]


class DispatchView(Vertical):
    """Command-centre: mode strip, ticket roster, usage, gauges, and calendar."""

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

    def compose(self) -> ComposeResult:
        yield ModeStrip()
        yield GaugeStrip()
        with Horizontal(id="dispatch_body"):
            yield ScheduleTicketsTable()
            yield CalendarPanel()

    def refresh_from_snapshot(
        self,
        snapshot: ScheduleSnapshot,
        *,
        usage_drill_in_loader: UsageDrillInLoader | None = None,
    ) -> None:
        """Refresh all dispatch sub-widgets from a service snapshot."""
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
