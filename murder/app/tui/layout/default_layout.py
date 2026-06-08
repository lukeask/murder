"""Default layout — reproduces the murder TUI exactly.

This module is the proof that "a design = a layout module over stores +
components."  It instantiates every StoreComponent widget, binds each to its
store(s) via bind_stores(), and exposes a compose() method for app.py.

Binding happens before compose() is called so that on_mount() (which runs
during Textual's compose/mount cycle) finds the stores already attached and
subscribes immediately.

Store → widget mapping
----------------------
Header          : dispatch, roster, schedule  (multi-store; overrides _render_from_stores)
TicketGrid      : dispatch
CrowsView       : roster
PlanList        : plans
NotesList       : notes
ReportsList     : reports
PlanDocument    : plans
NotesDocument   : notes
ReportDocument  : reports
ChatLog         : pane-tick only (conversation_id driven ad-hoc by app.py; see WAKEUP.md)
DispatchView    : schedule
EscalationStrip : escalations
PaneMirror      : pane-tick only (async capture; no store)
ChatInput       : pure UI leaf (no store)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer

from murder.app.tui.chat_input import ChatInput
from murder.app.tui.crows_view import CrowsView
from murder.app.tui.dispatch import DispatchView
from murder.app.tui.escalation_strip import EscalationStrip
from murder.app.tui.header import Header
from murder.app.tui.pane_capture import CapturePaneFn
from murder.app.tui.pane_mirror import PaneMirror
from murder.app.tui.perf_log import PerfLog
from murder.app.tui.planning_mode_widgets import (
    ChatLog,
    NotesDocument,
    NotesList,
    PlanDocument,
    PlanList,
    ReportDocument,
    ReportsList,
)
from murder.app.tui.ticket_grid import TicketGrid

if TYPE_CHECKING:
    from murder.app.tui.coordinator import IngestionCoordinator


class DefaultLayout:
    """Instantiates and binds all StoreComponent widgets for the default UI.

    Usage::

        layout = DefaultLayout(coordinator, runtime, ...)
        # In MurderApp.compose():
        yield from layout.compose()
        # Access widgets for action handlers:
        layout.header, layout.grid, layout.crows, ...
    """

    def __init__(
        self,
        coordinator: IngestionCoordinator,
        *,
        project_name: str,
        perf: PerfLog | None = None,
        capture_pane: CapturePaneFn | None = None,
        favorites_io: Any | None = None,
        usage_drill_in_loader: Any | None = None,
    ) -> None:
        self._coordinator = coordinator

        # ── Instantiate widgets ────────────────────────────────────────────
        self.header = Header(project_name)
        self.grid = TicketGrid()
        self.crows = CrowsView(
            perf_log=perf,
            capture_pane=capture_pane,
            favorites_io=favorites_io,
        )
        self.plans = PlanList()
        self.plan_doc = PlanDocument()
        self.notes_list = NotesList()
        self.notes_doc = NotesDocument()
        self.reports_list = ReportsList()
        self.report_doc = ReportDocument()
        self.collab_chat = ChatLog(agent_label="collaborator")
        self.dispatch = DispatchView()
        self.mirror = PaneMirror(perf=perf, capture_pane=capture_pane)
        self.escalations = EscalationStrip()
        self.chat = ChatInput()

        # Set the drill-in loader on GaugeStrip directly (injected once at
        # layout construction; DispatchView's store-driven refresh leaves it
        # untouched since usage_drill_in_loader=None on that path).
        if usage_drill_in_loader is not None:
            try:
                from murder.app.tui.dispatch.gauges import GaugeStrip  # noqa: PLC0415
                # GaugeStrip is a child of DispatchView — set it after compose
                # by storing the loader for deferred injection.
                self._gauge_drill_in_loader = usage_drill_in_loader
            except ImportError:
                self._gauge_drill_in_loader = None
        else:
            self._gauge_drill_in_loader = None

        # ── Bind stores ────────────────────────────────────────────────────
        # Binding MUST happen before compose()/mount so on_mount finds the
        # stores attached and subscribes immediately.

        # Header: multi-store — reads dispatch (attention counts + tickets),
        # roster (in-flight crows), and schedule (usage gauges).
        self.header.bind_stores(
            dispatch=coordinator.dispatch,
            roster=coordinator.roster,
            schedule=coordinator.schedule,
        )

        # TicketGrid: dispatch snapshot → ticket rows.
        self.grid.bind_stores(dispatch=coordinator.dispatch)

        # CrowsView: roster snapshot → entries/tiles.
        self.crows.bind_stores(roster=coordinator.roster)

        # Planning lists: each binds to its document store.
        self.plans.bind_stores(plans=coordinator.plans)
        self.notes_list.bind_stores(notes=coordinator.notes)
        self.reports_list.bind_stores(reports=coordinator.reports)

        # Planning documents: bind to same document store as the list —
        # refresh_from_snapshot reads selected_name + bodies from the snapshot.
        self.plan_doc.bind_stores(plans=coordinator.plans)
        self.notes_doc.bind_stores(notes=coordinator.notes)
        self.report_doc.bind_stores(reports=coordinator.reports)

        # ChatLog: NOT bound to the conversations store.
        # The planning chat conversation_id switches dynamically between
        # "planner-{name}" and "collaborator-0" based on which planner target is
        # selected, and the render includes status placeholders ("no planner session
        # yet", "nothing parsed yet") that are not available from the store snapshot.
        # app.py drives collab_chat via the ad-hoc path (set_turns / replace_transcript)
        # using conversations.doc_for() directly.  This is documented as a Phase 3
        # follow-up: once the status strings are modelled in the store, collab_chat
        # can call set_conversation_id() and self-subscribe.

        # DispatchView: schedule snapshot → cascades to all child widgets.
        self.dispatch.bind_stores(schedule=coordinator.schedule)

        # EscalationStrip: escalations snapshot.
        self.escalations.bind_stores(escalations=coordinator.escalations)

        # PaneMirror: intentionally left on the pane-tick path.
        # Reason: PaneMirror is an async capture-pane consumer, not a
        # poll-snapshot consumer.  It attaches to a specific tmux session that
        # changes dynamically (crow selection, plan selection) and renders live
        # by calling capture_pane() on each tick.  A TailStore would need a
        # feeder in coordinator.pane_tick that produces a per-session snapshot
        # — plausible but adds complexity for little gain since the mirror is
        # not hot-reloaded across views.  This is documented for Phase 3.
        # (See also the rationale in pane_mirror.py for the t055 decision.)

        # ChatInput: pure UI leaf — no data store.

    def inject_gauge_drill_in_loader(self) -> None:
        """Set the usage drill-in loader on GaugeStrip after compose.

        Must be called after the DispatchView is mounted (so GaugeStrip
        exists as a child).  Called by app.py in on_mount.
        """
        if self._gauge_drill_in_loader is None:
            return
        try:
            from murder.app.tui.dispatch.gauges import GaugeStrip  # noqa: PLC0415
            gauge = self.dispatch.query_one(GaugeStrip)
            gauge.set_drill_in_loader(self._gauge_drill_in_loader)
        except Exception:
            pass

    def compose(self) -> ComposeResult:
        """Yield the full widget tree in the correct order.

        This is called from MurderApp.compose() via ``yield from layout.compose()``.
        """
        yield self.header
        with Horizontal(id="body"):
            yield self.grid
            yield self.crows
            with Vertical(id="planning_sidebar"):
                yield self.plans
                yield self.notes_list
                yield self.reports_list
            yield self.plan_doc
            yield self.notes_doc
            yield self.report_doc
            yield self.collab_chat
            yield self.dispatch
            yield self.mirror
        yield self.escalations
        yield self.chat
        yield Footer()
