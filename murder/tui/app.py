"""Top-level Textual app — wires header, ticket grid, pane mirror, and
escalation strip onto the running service client."""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.notifications import SeverityLevel
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Footer, Static

from murder.config import Config
from murder.orchestration.orchestrator import is_rogue_agent_id
from murder.service.client_api import EscalationSummary
from murder.service.settings_service import SettingsService
from murder.storage.paths import ticket_md, tickets_dir, tui_prefs_path
from murder.storage.worktrees import list_murder_worktrees_sync
from murder.terminal.session_names import format_session_name
from murder.tui.chat_input import ChatInput
from murder.tui.chat_target_cycle import (
    ChatTarget,
    crows_chat_targets,
    cycle_chat_target,
    planning_chat_targets,
)
from murder.tui.controllers import DispatchController, TuiContext
from murder.tui.crows_view import CrowsView, CrowTile
from murder.tui.dispatch import DispatchView, ScheduleTicketsTable
from murder.tui.dispatch.calendar import CalendarPanel
from murder.tui.dispatch.gauges import GaugeStrip
from murder.tui.dispatch.mode_strip import ModeStrip
from murder.tui.escalation_resolve_wizard import EscalationResolveWizard
from murder.tui.escalation_strip import EscalationStrip
from murder.tui.header import Header
from murder.tui.note_capture import RECENT_NOTE_ROWS, NoteCaptureScreen
from murder.tui.pane_capture import CapturePaneFn
from murder.tui.pane_mirror import PaneMirror
from murder.tui.perf_log import make_perf_log
from murder.tui.planning_mode_widgets import (
    ChatLog,
    NotesDocument,
    NotesList,
    PlanDocument,
    PlanList,
    ReportDocument,
    ReportsList,
)
from murder.tui.settings_screen import SettingsScreen
from murder.tui.spawn_wizard import SpawnWizard, build_worktree_options
from murder.tui.themes import crow_tui_variable_defaults, register_crow_themes
from murder.tui.ticket_grid import TicketGrid
from murder.user_config import UserConfig, load_user_config

if TYPE_CHECKING:
    from typing import Any


COLLABORATOR_START_TIMEOUT_S = 120.0
CTRL_C_DOUBLE_TAP_S = 1.5
TOAST_TIMEOUT_MULTIPLIER = 3.0
RENAME_SELECTED_ARG_COUNT = 2

_COLON_RAW_KEYS: dict[str, str] = {
    ":uparrow": "Up",
    ":downarrow": "Down",
    ":larrow": "Left",
    ":rarrow": "Right",
    ":enter": "Enter",
}

_TNUM_RE = re.compile(r"^t(\d+)$", re.IGNORECASE)


def _next_ticket_id(repo_root: Path) -> str:
    """Return the next t<NNN> id, scanning the tickets dir for the current max."""
    root = tickets_dir(repo_root)
    max_n = 0
    if root.exists():
        for p in root.glob("*.md"):
            m = _TNUM_RE.match(p.stem)
            if m:
                max_n = max(max_n, int(m.group(1)))
    return f"t{max_n + 1:03d}"


def _is_ticket_handle(handle: str, repo_root: Path) -> bool:
    if _TNUM_RE.match(handle):
        return True
    return (tickets_dir(repo_root) / f"{handle}.yaml").exists()


def _git_head_sha(repo_root: Path) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
        return proc.stdout.strip() if proc.returncode == 0 else ""
    except Exception:
        return ""


class HelpScreen(ModalScreen[None]):
    """Small in-app glossary and key reference."""

    BINDINGS = [("escape", "dismiss", "Close"), ("ctrl+/", "dismiss", "Close")]
    CSS = """
    HelpScreen {
        align: center middle;
    }
    #help {
        width: 74;
        max-width: 90%;
        border: solid $primary;
        background: $surface;
        padding: 1 2;
    }
    """

    def compose(self) -> ComposeResult:
        yield Container(
            Static(
                "\n".join(
                    [
                        "[b]murder help[/b]",
                        "",
                        "crow: coding agent assigned to one ticket",
                        "crow_handler: watches a crow and records progress",
                        "planning_agent: per-plan LLM that answers crow questions",
                        "ticket: scoped unit of work with deps, write_set, checklist",
                        "wave: tickets that may run after earlier dependencies finish",
                        "",
                        "[b]commands[/b]",
                        "!<cmd>         run shell command (output in pane mirror)",
                        ":wq :q :q!    quit murder",
                        ":ticket <title>  create PLANNED ticket (sync picks up in ~2s)",
                        ":quick <title>   create ticket and kick immediately",
                        ":rename <new>    rename selected plan, or rogue crow when chatting one",
                        "  (:rename <old> <new> for plans)",
                        ":deprecate [name]  deprecate selected or named plan",
                        ":spawn :s       open rogue crow spawn wizard",
                        ":hideescalations  toggle escalation strip",
                        ":uparrow :downarrow :larrow :rarrow :enter  one raw key → harness",
                        ":raw  pass keys through until Esc Esc",
                        "/anything      passed to agent unchanged (/clear /compact etc)",
                        "",
                        "[b]keys[/b]",
                        "ctrl+f focus chat · ctrl+h/l cycle chat target (planning/crows) · ? help · ctrl+, settings",
                        "ctrl+1/2/3  switch views  ·  [ and ] cycle views",
                        "Dispatch: [b]c[/b] / Enter opens ticket metadata editor; "
                        "F6 kicks ready rows",
                        "ctrl+b  toggle docs sidebar (planning) / crows roster sidebar (crows view)",
                        "ctrl+y  planning/crow chat pane: parsed ⇄ raw tmux (when pane focused)",
                        "ctrl+c twice  force quit  ·  escape  unfocus chat",
                        "j/k or ↑/↓  vim-style navigation in lists and logs",
                        "ctrl+r refresh (includes usage gauges)",
                        "ctrl+n  quick note capture overlay (global)",
                        "e  focus escalation strip (when active) · a solve · r retry · ↵ navigate",
                        "murder --help shows the CLI reference",
                    ]
                ),
                id="help",
            )
        )

    def action_dismiss(self) -> None:
        self.dismiss()


class MurderApp(App[None]):
    """Single-screen TUI with planning, crows, and schedule views."""

    TITLE = "murder"
    ENABLE_COMMAND_PALETTE = False

    BINDINGS = [
        Binding("ctrl+c", "ctrl_c_quit", "Quit", priority=True, show=False),
        ("ctrl+comma", "open_settings", "Settings"),
        Binding("ctrl+n", "open_note_capture", "Quick note", show=False),
        Binding("ctrl+p", "new_plan_session", "New plan", show=False),
        Binding("ctrl+1", "view_planning", "Planning", priority=True),
        Binding("ctrl+2", "view_crows", "Crows", priority=True),
        Binding("ctrl+3", "view_schedule", "Dispatch", priority=True),
        Binding("[", "previous_view", "Prev view", priority=True, show=False),
        Binding("]", "next_view", "Next view", priority=True, show=False),
        ("ctrl+b", "toggle_sidebar", "Docs sidebar"),
        ("ctrl+y", "toggle_collab_raw", "Raw pane"),
        # Between-pane focus (VISION §4.3). Bare hjkl/arrows stay with the
        # focused widget so intra-pane motion isn't stolen.
        # priority=True so these fire even when a TextArea/Input is focused
        # (TextArea binds ctrl+left/right for word motion; ctrl+k for line
        # deletion — without priority the widget consumes them first).
        Binding("ctrl+h", "focus_left", "Focus left", show=False, priority=True),
        Binding("ctrl+j", "focus_down", "Focus down", show=False, priority=True),
        Binding("ctrl+k", "focus_up", "Focus up", show=False, priority=True),
        Binding("ctrl+l", "focus_right", "Focus right", show=False, priority=True),
        Binding("ctrl+left", "focus_left", "Focus left", show=False, priority=True),
        Binding("ctrl+down", "focus_down", "Focus down", show=False, priority=True),
        Binding("ctrl+up", "focus_up", "Focus up", show=False, priority=True),
        Binding("ctrl+right", "focus_right", "Focus right", show=False, priority=True),
        Binding("tab", "focus_next_region", "Next pane", show=False, priority=True),
        Binding("shift+tab", "focus_previous_region", "Prev pane", show=False, priority=True),
        Binding("e", "focus_escalations", "Escalations", show=False),
        Binding("ctrl+e", "focus_escalations", "Escalations", show=False),
        ("ctrl+r", "refresh_now", "Refresh"),
        ("c", "schedule_apply_carve", "Metadata"),
        Binding("m", "schedule_mode_picker", "Mode", show=False),
        ("f6", "kick_ready", "Kick"),
        ("ctrl+f", "focus_chat", "Chat"),
        Binding("ctrl+s", "quick_spawn", "Spawn", show=False, priority=True),
        Binding("ctrl+/", "show_help_force", "Help", show=False),
        ("?", "show_help_force", "Help"),
    ]

    CSS = """
    Screen {
        layout: vertical;
    }
    ToastRack {
        align: right bottom;
    }
    #body {
        height: 1fr;
    }
    TicketGrid {
        width: 50%;
        border: solid $border;
    }
    CrowsView {
        width: 1fr;
    }
    #planning_sidebar {
        width: 18%;
        height: 1fr;
    }
    PlanList {
        height: 1fr;
        border: solid $border;
    }
    NotesList {
        height: 1fr;
        border: solid $border;
    }
    ReportsList {
        height: 1fr;
        border: solid $border;
    }

    /* Focused pane — $pane-focus is distinct from crow-health-* (see themes.py). */
    PlanList:focus,
    NotesList:focus,
    ReportsList:focus,
    PlanDocument:focus,
    NotesDocument:focus,
    ReportDocument:focus,
    ChatLog:focus,
    PaneMirror:focus,
    EscalationStrip:focus,
    CalendarPanel:focus,
    GaugeStrip:focus,
    ModeStrip:focus,
    TicketGrid:focus {
        border: heavy $pane-focus;
    }

    PlanList:focus,
    NotesList:focus,
    ReportsList:focus,
    ChatLog:focus,
    PaneMirror:focus,
    CalendarPanel:focus,
    TicketGrid:focus {
        background-tint: 0%;
    }
    """

    VIEWS = ("planning", "crows", "schedule")

    def __init__(self, runtime: Any) -> None:
        super().__init__()
        self.runtime = runtime
        self.perf = make_perf_log(runtime.repo_root)
        self._perf_log_enabled = self.perf.enabled
        self._perf_mount_time: float | None = None
        self._header = Header(runtime.config.project.name)
        self._grid = TicketGrid()
        self._bus_capture_pane: CapturePaneFn = self._capture_pane_via_bus
        self._crows = CrowsView(
            perf_log=self.perf,
            capture_pane=self._bus_capture_pane,
            prefs_path=tui_prefs_path(runtime.repo_root),
        )
        self._plans = PlanList()
        self._plan_doc = PlanDocument()
        self._notes_list = NotesList()
        self._notes_doc = NotesDocument()
        self._reports_list = ReportsList()
        self._report_doc = ReportDocument()
        self._collab_chat = ChatLog(agent_label="collaborator")
        self._dispatch = DispatchView()
        self._mirror = PaneMirror(perf=self.perf, capture_pane=self._bus_capture_pane)
        self._raw_key_mode = False
        self._escalations = EscalationStrip()
        self._escalation_wizard: EscalationResolveWizard | None = None
        self._chat = ChatInput()
        self._spawn_wizard: SpawnWizard | None = None
        self._collab_lock = asyncio.Lock()
        self._collab_chat_lock = asyncio.Lock()
        self._sidebar_visible = True
        self._escalations_visible = True
        self._collab_raw = False  # ctrl+y: show the raw tmux pane instead of the parsed chat
        self._chat_target_agent_id: str | None = None
        self._chat_target_label = "collaborator"
        self._chat_pending_message: str | None = None
        self._user_config: UserConfig = load_user_config()
        self._view = "planning"
        self._pre_chat_focus = None
        self._has_selected_plan = False
        self._active_document = "plan"
        self._shell_session: str | None = None
        self._last_ctrl_c: float = 0.0
        self._note_capture_draft = ""
        self._chat_input_memory = ""
        self._crow_snapshot = None
        self._dispatch_ctrl = DispatchController(
            TuiContext(
                submit_command=self._submit_command,
                notify=self.notify,
                refresh_views=self._refresh_service_views,
                push_screen=self.push_screen,
                run_worker=self.run_worker,
                get_ticket_status=runtime.get_ticket_status,
                get_ticket_carve_snapshot=runtime.get_ticket_carve_snapshot,
            )
        )
        register_crow_themes(self)

    def get_theme_variable_defaults(self) -> dict[str, str]:
        return crow_tui_variable_defaults()

    def notify(
        self,
        message: str,
        *,
        title: str = "",
        severity: SeverityLevel = "information",
        timeout: float | None = None,
        markup: bool = True,
    ) -> None:
        if timeout is not None:
            timeout *= TOAST_TIMEOUT_MULTIPLIER
        super().notify(
            message,
            title=title,
            severity=severity,
            timeout=timeout,
            markup=markup,
        )

    def compose(self) -> ComposeResult:
        yield self._header
        with Horizontal(id="body"):
            yield self._grid
            yield self._crows
            with Vertical(id="planning_sidebar"):
                yield self._plans
                yield self._notes_list
                yield self._reports_list
            yield self._plan_doc
            yield self._notes_doc
            yield self._report_doc
            yield self._collab_chat
            yield self._dispatch
            yield self._mirror
        yield self._escalations
        yield self._chat
        yield Footer()

    def on_mount(self) -> None:
        if self._user_config.tui.theme in self.available_themes:
            self.theme = self._user_config.tui.theme
        self.sub_title = str(self.runtime.repo_root)
        self._apply_mode()
        if self.perf.enabled:
            self._perf_mount_time = time.perf_counter()
            refresh_ms = self.runtime.config.tui.refresh_ms
            interval_s = max(refresh_ms, 250) / 1000
            pane_interval_s = max(interval_s, 1.0)
            self.perf.event(
                "tui.startup",
                refresh_ms=refresh_ms,
                pane_interval_s=round(pane_interval_s, 3),
                git_sha=_git_head_sha(self.runtime.repo_root),
                pid=os.getpid(),
            )
        self._refresh_service_views()
        self.set_focus(self._chat)
        if self.runtime.config.project.name == "TODO_SET_ME":
            self.notify(
                "Project name is unset — open Settings (ctrl+,) to update roles.yaml.",
                severity="warning",
                timeout=10,
            )
        interval_s = max(self.runtime.config.tui.refresh_ms, 250) / 1000
        self.set_interval(interval_s, self._refresh_service_views)
        self.set_interval(max(interval_s, 1.0), self._refresh_pane)

    def on_unmount(self) -> None:
        if self.perf.enabled and self._perf_mount_time is not None:
            self.perf.event(
                "tui.shutdown",
                uptime_s=round(time.perf_counter() - self._perf_mount_time, 3),
            )
        self.perf.close()

    def action_ctrl_c_quit(self) -> None:
        now = time.monotonic()
        if now - self._last_ctrl_c < CTRL_C_DOUBLE_TAP_S:
            self.exit()
        else:
            self._last_ctrl_c = now
            self.notify("Press ctrl+c again to quit", timeout=2)

    def action_refresh_now(self) -> None:
        if self._insert_if_chat_focused("r"):
            return
        self.run_worker(self._refresh_now(), exclusive=True, group="refresh")

    def action_focus_escalations(self) -> None:
        if self._escalations.display:
            self._escalations.focus()
        else:
            self.notify("No active escalations.", timeout=2)

    async def _refresh_now(self) -> None:
        await self._dispatch_ctrl.sample_usage_snapshots()
        await self._refresh_bus_views()
        await self._mirror.refresh_pane()

    def _refresh_service_views(self) -> None:
        self.run_worker(
            self._refresh_bus_views(),
            exclusive=True,
            group="refresh",
            exit_on_error=False,
        )

    async def _refresh_bus_views(self) -> None:
        perf = self.perf
        with perf.span("tui.refresh_bus_views"):
            dispatch = await self.runtime.get_dispatch_snapshot()
            with perf.span("tui.crows.render_snapshot"):
                self._crow_snapshot = await self.runtime.get_crow_snapshot()
            with perf.span("tui.header.refresh_counts"):
                self._header.refresh_from_snapshot(
                    dispatch,
                    crow_snapshot=self._crow_snapshot,
                )
            with perf.span("tui.grid.refresh"):
                self._grid.refresh_from_snapshot(dispatch)
            with perf.span("tui.crows.render_snapshot"):
                self._crows.render_from_snapshot(self._crow_snapshot)
            with perf.span("tui.plans.refresh"):
                self._plans.refresh_from_snapshot(await self.runtime.get_plans_snapshot())
            with perf.span("tui.notes_list.refresh"):
                self._notes_list.refresh_from_snapshot(
                    await self.runtime.get_notes_snapshot()
                )
            with perf.span("tui.reports_list.refresh"):
                self._reports_list.refresh_from_snapshot(
                    await self.runtime.get_reports_snapshot()
                )
            with perf.span("tui.schedule.refresh"):
                self._dispatch.refresh_from_snapshot(
                    await self.runtime.get_schedule_snapshot(),
                    usage_drill_in_loader=self.runtime.get_usage_gauge_drill_in,
                )
            with perf.span("tui.escalations.refresh"):
                self._escalations.refresh_from_snapshot(
                    await self.runtime.get_escalations(),
                    show=self._escalations_visible,
                )
            if (
                self._view == "planning"
                and self._active_document == "plan"
                and self._plans.selected_name
            ):
                if self._has_selected_plan:
                    plan_name = self._plans.selected_name
                    self._chat_target_agent_id = f"planner-{plan_name}"
                    self._chat_target_label = f"planner: {plan_name}"
                    self._sync_chat_recipient()
                    self._sync_planner_mirror_session(plan_name)
                self.run_worker(
                    self._render_plan(self._plans.selected_name),
                    exclusive=True,
                    group="plandoc",
                    exit_on_error=False,
                )
            elif (
                self._view == "planning"
                and self._active_document == "note"
                and self._notes_list.selected_name
            ):
                self.run_worker(
                    self._render_note(self._notes_list.selected_name),
                    exclusive=True,
                    group="notedoc",
                    exit_on_error=False,
                )
            elif (
                self._view == "planning"
                and self._active_document == "report"
                and self._reports_list.selected_name
            ):
                self.run_worker(
                    self._render_report(self._reports_list.selected_name),
                    exclusive=True,
                    group="reportdoc",
                    exit_on_error=False,
                )
            elif self._view == "planning" and self._active_document == "plan":
                self._has_selected_plan = False
                self._chat_target_agent_id = None
                self._chat_target_label = "collaborator"
                self._sync_chat_recipient()
                self._plan_doc.display = False

    def _refresh_pane(self) -> None:
        # Run in a worker with exit_on_error=False so a transient bus hiccup
        # (e.g. a slow capture_pane RPC raising TimeoutError) skips the tick
        # instead of propagating into the message pump and crashing the TUI.
        # Dedicated group + exclusive coalesces overlapping ticks without
        # cross-cancelling the manual group="mirror" refreshes.
        self.run_worker(
            self._refresh_pane_views(),
            exclusive=True,
            group="pane_refresh",
            exit_on_error=False,
        )

    async def _refresh_pane_views(self) -> None:
        with self.perf.span("tui.refresh_pane"):
            await self._mirror.refresh_pane()
            if self._view == "crows":
                await self._crows.refresh_tails()
            if self._view == "planning" and not self._collab_raw:
                await self._refresh_planning_chat()

    def on_ticket_grid_ticket_selected(self, event: TicketGrid.TicketSelected) -> None:
        self._mirror.set_session(self._crow_session_for_ticket(event.ticket_id))
        self.run_worker(self._mirror.refresh_pane(), exclusive=True, group="mirror")

    def on_crows_view_tile_selected(self, event: CrowsView.TileSelected) -> None:
        # Keep the shared pane mirror in sync so planning's collab-raw
        # toggle and the shell session share a hint.
        self._mirror.set_session(event.entry.session)
        if self._view == "crows":
            if event.entry.agent_id != self._chat_target_agent_id:
                self._chat_pending_message = None
            self._chat_target_agent_id = event.entry.agent_id
            label = event.entry.ticket_id or event.entry.session or event.entry.agent_id
            self._chat_target_label = label
            self._sync_chat_recipient()

    def on_crows_view_kill_requested(self, event: CrowsView.KillRequested) -> None:
        self.run_worker(
            self._murder_crow(event.agent_id),
            exclusive=False,
            group="ui_crow_kill",
        )

    async def _murder_crow(self, agent_id: str) -> None:
        result = await self._submit_command(
            target_worker="orchestrator",
            kind="agent.stop",
            payload={"agent_id": agent_id},
            timeout_s=15.0,
        )
        if result is None:
            return
        if result.get("handled") is False:
            error = str(result.get("error") or "stop failed")
            self.notify(error, severity="error", timeout=6)
            return
        if self._chat_target_agent_id == agent_id:
            self._chat_target_agent_id = None
            self._chat_target_label = "collaborator"
            self._chat_pending_message = None
            self._sync_chat_recipient()
        self.notify(f"murdered {agent_id}", timeout=2)
        self._refresh_service_views()

    def on_crow_tile_opened(self, event: CrowTile.Opened) -> None:
        # The CrowsView itself handles enlarge; surface a short attach hint
        # so power users can still drop into a real tmux session.
        hint = f"tmux attach -t {event.entry.session}" if event.entry.session else "(no session)"
        self.notify(f"attach: {hint}", timeout=6)

    def on_plan_list_plan_highlighted(self, event: PlanList.PlanHighlighted) -> None:
        if self._view == "planning":
            self._active_document = "plan"
            if not self._has_selected_plan:
                self._has_selected_plan = True
            self._chat_target_agent_id = f"planner-{event.name}"
            self._chat_target_label = f"planner: {event.name}"
            self._apply_mode()
            self.run_worker(
                self._render_plan(event.name),
                exclusive=True,
                group="plandoc",
                exit_on_error=False,
            )

    async def on_plan_list_plan_opened(self, event: PlanList.PlanOpened) -> None:
        await self._open_plan(event.name)

    async def on_plan_list_plan_deprecate_requested(
        self, event: PlanList.PlanDeprecateRequested
    ) -> None:
        await self._deprecate_plan(event.name)

    def on_notes_list_note_highlighted(self, event: NotesList.NoteHighlighted) -> None:
        if self._view == "planning":
            self._active_document = "note"
            self._chat_target_agent_id = None
            self._chat_target_label = "collaborator"
            self._apply_mode()
            self.run_worker(
                self._render_note(event.name),
                exclusive=True,
                group="notedoc",
                exit_on_error=False,
            )

    async def on_notes_list_note_opened(self, event: NotesList.NoteOpened) -> None:
        await self._open_note(event.name)

    async def on_notes_list_note_retire_requested(
        self, event: NotesList.NoteRetireRequested
    ) -> None:
        await self._retire_note(event.name)

    def on_reports_list_report_highlighted(self, event: ReportsList.ReportHighlighted) -> None:
        if self._view == "planning":
            self._active_document = "report"
            self._chat_target_agent_id = None
            self._chat_target_label = "collaborator"
            self._apply_mode()
            self.run_worker(
                self._render_report(event.name),
                exclusive=True,
                group="reportdoc",
                exit_on_error=False,
            )

    async def on_reports_list_report_opened(self, event: ReportsList.ReportOpened) -> None:
        await self._open_report(event.name)

    async def _render_plan(self, name: str) -> None:
        with self.perf.span("tui.render_plan"):
            display = await self.runtime.get_plan_display(name)
            if display is None:
                return
            await self._plan_doc.set_plan_markdown(name, display.markdown)

    async def _open_plan(self, name: str) -> None:
        await self.runtime.reconcile_plan(name)
        path = await self.runtime.plan_path_for(name)
        with self.suspend():
            code = self.runtime.open_editor_blocking(path, self._user_config.tui.editor)
        if code != 0:
            self.notify(f"editor exited with {code}", severity="warning", timeout=5)
        await self.runtime.reconcile_plan(name)
        self._refresh_service_views()
        await self._render_plan(name)

    async def _render_note(self, name: str) -> None:
        with self.perf.span("tui.render_note"):
            display = await self.runtime.get_note_display(name)
            if display is None:
                return
            await self._notes_doc.show(name, display.markdown)

    async def _open_note(self, name: str) -> None:
        result = await self._submit_command(
            target_worker="orchestrator",
            kind="note.ensure",
            payload={"name": name},
            timeout_s=10.0,
        )
        if result is None:
            return
        mat_path = str(result.get("materialized_path") or "")
        path = (
            self.runtime.repo_root / mat_path
            if mat_path
            else await self.runtime.note_path_for(name)
        )
        with self.suspend():
            code = self.runtime.open_editor_blocking(path, self._user_config.tui.editor)
        if code != 0:
            self.notify(f"editor exited with {code}", severity="warning", timeout=5)
        if self.runtime.note_sync is not None:
            await self.runtime.note_sync.reconcile_file(path)
        self._refresh_service_views()
        await self._render_note(name)
        self.set_focus(self._notes_doc)

    async def _render_report(self, name: str) -> None:
        with self.perf.span("tui.render_report"):
            display = await self.runtime.get_report_display(name)
            if display is None:
                return
            await self._report_doc.show(name, display.markdown)

    async def _open_report(self, name: str) -> None:
        path = await self.runtime.report_path_for(name)
        with self.suspend():
            code = self.runtime.open_editor_blocking(path, self._user_config.tui.editor)
        if code != 0:
            self.notify(f"editor exited with {code}", severity="warning", timeout=5)
        self._refresh_service_views()
        await self._render_report(name)
        self.set_focus(self._report_doc)

    async def _retire_note(self, name: str) -> None:
        result = await self._submit_command(
            target_worker="orchestrator",
            kind="note.retire",
            payload={"name": name},
            timeout_s=10.0,
        )
        if result is None:
            return
        dest_name = str(result.get("dest_name") or name)
        self._refresh_service_views()
        self.notify(f"retired note: {dest_name}", timeout=4)
        if self._notes_list.selected_name:
            await self._render_note(self._notes_list.selected_name)
        else:
            self._active_document = "plan"
            self._apply_mode()

    async def _deprecate_plan(self, name: str) -> None:
        result = await self._submit_command(
            target_worker="orchestrator",
            kind="plan.deprecate",
            payload={"name": name},
            timeout_s=10.0,
        )
        if result is None:
            return
        self._refresh_service_views()
        self.notify(f"deprecated plan: {name}", timeout=4)
        selected = self._plans.selected_name
        if selected:
            await self._focus_plan(selected)
        else:
            self._has_selected_plan = False
            self._chat_target_agent_id = None
            self._chat_target_label = "collaborator"
            self._chat.set_recipient("collaborator")
            self._plan_doc.display = False
            self._apply_mode()

    async def _refresh_planning_chat(self) -> None:
        """Re-parse the active planning chat pane (collaborator or planner).

        Lock-guarded so overlapping refresh-pane ticks don't race two parses
        onto the same conversation log.
        """
        if self._collab_chat_lock.locked():
            return
        async with self._collab_chat_lock:
            with self.perf.span("tui.planning_chat.refresh"):
                if self._view != "planning":
                    return
                plan_name = self._planner_target_name()
                if plan_name is not None:
                    await self._refresh_planner_chat(plan_name)
                else:
                    await self._refresh_collaborator_chat()

    async def _refresh_collaborator_chat(self) -> None:
        self._sync_planning_chat_label()
        result = await self._submit_command(
            target_worker="collaborator",
            kind="collaborator.transcript.refresh",
            payload={},
            timeout_s=8.0,
            notify_errors=False,
        )
        if result is None:
            return
        if not bool(result.get("available")):
            self._collab_chat.replace_transcript(
                [],
                status="(no collaborator yet — type a message to start one)",
            )
            return
        turns = [
            (str(item.get("role", "")), str(item.get("text", "")))
            for item in result.get("turns", [])
            if isinstance(item, dict)
        ]
        if turns:
            self._collab_chat.set_turns(turns)
            return
        if bool(result.get("has_parser")):
            self._collab_chat.replace_transcript(
                [],
                status="(collaborator chat — nothing parsed yet)",
            )
        else:
            harness_kind = str(result.get("harness_kind", "unknown"))
            self._collab_chat.replace_transcript(
                [],
                status=(
                    f"(no transcript parser for '{harness_kind}' yet — "
                    "press ctrl+y for the raw pane)"
                ),
            )

    async def _refresh_planner_chat(self, plan_name: str) -> None:
        self._sync_planning_chat_label()
        result = await self._submit_command(
            target_worker="collaborator",
            kind="collaborator.transcript.refresh",
            payload={"agent_id": f"planner-{plan_name}"},
            timeout_s=8.0,
            notify_errors=False,
        )
        if result is None:
            return
        if not bool(result.get("available")):
            self._collab_chat.replace_transcript([], status="(no planner session yet)")
            return
        turns = [
            (str(item.get("role", "")), str(item.get("text", "")))
            for item in result.get("turns", [])
            if isinstance(item, dict)
        ]
        if turns:
            self._collab_chat.set_turns(turns)
            return
        if bool(result.get("has_parser")):
            self._collab_chat.replace_transcript(
                [],
                status="(planner chat — nothing parsed yet)",
            )
        else:
            harness_kind = str(result.get("harness_kind", "unknown"))
            self._collab_chat.replace_transcript(
                [],
                status=(
                    f"(no transcript parser for '{harness_kind}' yet — "
                    "press ctrl+y for the raw pane)"
                ),
            )

    def _sync_planning_chat_label(self) -> None:
        plan_name = self._planner_target_name()
        label = f"planner: {plan_name}" if plan_name is not None else "collaborator"
        self._collab_chat.set_agent_label(label)

    def action_view_planning(self) -> None:
        self._set_view("planning")

    def action_view_crows(self) -> None:
        self._set_view("crows")

    def action_view_schedule(self) -> None:
        self._set_view("schedule")
        with contextlib.suppress(Exception):
            table = self._dispatch.query_one(ScheduleTicketsTable)
            self.set_focus(table)
        self.run_worker(self._on_schedule_view_enter(), exclusive=True, group="usage")

    async def _on_schedule_view_enter(self) -> None:
        await self._dispatch_ctrl.probe_usage_on_schedule_enter()

    def action_next_view(self) -> None:
        if self._insert_if_chat_focused("]"):
            return
        idx = self.VIEWS.index(self._view)
        self._set_view(self.VIEWS[(idx + 1) % len(self.VIEWS)])

    def action_previous_view(self) -> None:
        if self._insert_if_chat_focused("["):
            return
        idx = self.VIEWS.index(self._view)
        self._set_view(self.VIEWS[(idx - 1) % len(self.VIEWS)])

    def _set_view(self, view: str) -> None:
        self._view = view
        self._chat_target_agent_id = None
        self._chat_target_label = "collaborator"
        self._chat_pending_message = None
        self._apply_mode()
        self._refresh_service_views()  # also re-renders the planning doc when a plan is selected

    def _apply_mode(self) -> None:
        planning = self._view == "planning"
        collab_chat_on = planning and not self._collab_raw
        collab_raw_on = planning and self._collab_raw
        if self._view in ("planning", "crows"):
            self._sync_chat_recipient()
        self._header.set_view(self._view)
        self._grid.display = False
        self._crows.display = self._view == "crows"
        with contextlib.suppress(Exception):
            self.query_one("#planning_sidebar").display = planning and self._sidebar_visible
        self._plans.display = planning and self._sidebar_visible
        self._notes_list.display = planning and self._sidebar_visible
        self._reports_list.display = planning and self._sidebar_visible
        self._plan_doc.display = (
            planning and self._active_document == "plan" and self._has_selected_plan
        )
        self._notes_doc.display = planning and self._active_document == "note"
        self._report_doc.display = planning and self._active_document == "report"
        self._collab_chat.display = collab_chat_on
        self._dispatch.display = self._view == "schedule"
        wizard_active = self._spawn_wizard is not None or self._escalation_wizard is not None
        self._chat.display = self._view != "schedule" and not wizard_active
        # The shared PaneMirror is now only used by planning's collab-raw
        # toggle; CrowsView owns its own mirror for the enlarged tile.
        self._mirror.display = collab_raw_on
        self._mirror.styles.width = "1fr"
        if collab_raw_on:
            plan_name = self._planner_target_name()
            if plan_name is not None:
                self._sync_planner_mirror_session(plan_name)
            else:
                self._sync_collaborator_mirror_session()
        if collab_chat_on:
            self.run_worker(self._refresh_planning_chat(), exclusive=True, group="collab_chat")
        self.refresh_bindings()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        del parameters
        if action in {"toggle_sidebar", "toggle_collab_raw"}:
            return self._view in ("planning", "crows")
        if action in {"schedule_apply_carve", "kick_ready"}:
            return self._view == "schedule"
        if action == "focus_chat":
            return self._view != "schedule"
        return True

    def _planning_chat_pane_focused(self) -> bool:
        return self._focus_contains(self._collab_chat) or self._focus_contains(self._mirror)

    def action_toggle_sidebar(self) -> None:
        if self._view == "crows":
            visible = self._crows.toggle_roster()
            self.notify(f"crows sidebar: {'on' if visible else 'off'}", timeout=2)
            return
        focused_before = self.focused
        self._sidebar_visible = not self._sidebar_visible
        self._apply_mode()
        if (
            not self._sidebar_visible
            and focused_before is not None
            and (
                focused_before is self._plans
                or focused_before is self._notes_list
                or focused_before is self._reports_list
                or focused_before in self._plans.walk_children()
                or focused_before in self._notes_list.walk_children()
                or focused_before in self._reports_list.walk_children()
            )
        ):
            self._focus_planning_after_sidebar_hide()
        self.notify(f"docs sidebar: {'on' if self._sidebar_visible else 'off'}", timeout=2)

    def action_toggle_collab_raw(self) -> None:
        if self._view == "crows":
            tile = self._focused_crow_tile()
            if tile is None:
                self.notify("focus a crow tail first", timeout=2)
                return
            tile.action_toggle_view()
            self.notify(
                f"crow tail view: {'raw tmux pane' if tile.raw_mode else 'parsed transcript'}",
                timeout=2,
            )
            return

        if self._view != "planning":
            return

        if not self._planning_chat_pane_focused():
            self.notify("focus the chat pane first", timeout=2)
            return

        self._collab_raw = not self._collab_raw
        if self._collab_raw:
            plan_name = self._planner_target_name()
            if plan_name is not None:
                self._sync_planner_mirror_session(plan_name)
            else:
                self._sync_collaborator_mirror_session()
        self._apply_mode()
        plan_name = self._planner_target_name()
        target = f"planner: {plan_name}" if plan_name is not None else "collaborator"
        self.notify(
            f"{target} view: {'raw tmux pane' if self._collab_raw else 'parsed chat'}",
            timeout=2,
        )

    def _focused_crow_tile(self) -> CrowTile | None:
        focused = self.focused
        if isinstance(focused, CrowTile):
            return focused
        if self._view != "crows":
            return None
        if self._crows.focus_last_tile() or self._crows.focus_first_tile():
            focused = self.focused
            if isinstance(focused, CrowTile):
                return focused
        return None

    def on_chat_input_user_message(self, event: ChatInput.UserMessage) -> None:
        # Own worker group: a chat dispatch may spend a minute inside
        # ensure_collaborator(); it must not be cancelled by an unrelated
        # exclusive=True UI worker (plan/notes render, pane mirror, …) in the
        # default group — that cancellation raises CancelledError, which is a
        # BaseException and slips past the handlers below, killing the spawn
        # silently.
        self.run_worker(self._dispatch_chat(event.text), exclusive=False, group="chat")

    def on_chat_input_spawn_command(self, event: ChatInput.SpawnCommand) -> None:
        event.stop()
        if self._spawn_wizard is not None:
            self._spawn_wizard.focus()
            return
        parent = self._chat.parent
        if parent is None:
            self.notify("spawn wizard unavailable", severity="error", timeout=4)
            return
        self._chat.display = False
        try:
            worktree_entries = list_murder_worktrees_sync(self.runtime.repo_root)
        except Exception:
            worktree_entries = []
        worktree_options = build_worktree_options(self.runtime.repo_root, worktree_entries)
        wizard = SpawnWizard(
            worktree_options=worktree_options,
            model_discovery=self._discover_spawn_models,
        )
        self._spawn_wizard = wizard
        parent.mount(wizard, before=self._chat)
        wizard.focus()

    async def _discover_spawn_models(self, harness: str):
        result = await SettingsService(self.runtime.repo_root).discover_models(harness)
        if not result.ok:
            self.notify(
                f"{harness} model discovery failed: {result.message or 'no models found'}",
                severity="warning",
                timeout=6,
            )
        return result

    def on_spawn_wizard_confirmed(self, event: SpawnWizard.Confirmed) -> None:
        event.stop()
        self._teardown_spawn_wizard()
        self.run_worker(
            self._do_spawn_rogue(
                event.harness,
                event.model,
                event.name,
                worktree_path=event.worktree_path,
                worktree_branch=event.worktree_branch,
            ),
            exclusive=False,
            group="spawn_rogue",
        )

    def on_spawn_wizard_cancelled(self, event: SpawnWizard.Cancelled) -> None:
        event.stop()
        self._teardown_spawn_wizard()

    def _teardown_spawn_wizard(self) -> None:
        wizard = self._spawn_wizard
        if wizard is not None:
            wizard.remove()
            self._spawn_wizard = None
        self._chat.display = self._view != "schedule"
        if self._chat.display:
            self._chat.focus()

    async def _do_spawn_rogue(
        self,
        harness: str,
        model: str,
        name: str | None,
        *,
        worktree_path: str | None = None,
        worktree_branch: str | None = None,
    ) -> None:
        spawn_rogue = getattr(self.runtime, "spawn_rogue", None)
        try:
            if spawn_rogue is not None:
                agent_id = await spawn_rogue(
                    harness=harness,
                    model=model,
                    name=name,
                    worktree_path=worktree_path,
                    worktree_branch=worktree_branch,
                )
            else:
                payload: dict[str, object] = {
                    "harness": harness,
                    "model": model,
                    "name": name,
                }
                if worktree_path is not None:
                    payload["worktree_path"] = worktree_path
                if worktree_branch is not None:
                    payload["worktree_branch"] = worktree_branch
                result = await self._submit_command(
                    target_worker="orchestrator",
                    kind="crow.spawn_rogue",
                    payload=payload,
                    timeout_s=120.0,
                    notify_errors=False,
                )
                if result is None:
                    self.notify("spawn failed: service unavailable", severity="error", timeout=8)
                    self._chat.focus()
                    return
                agent_id = str(result.get("agent_id") or "")
        except Exception as exc:
            self.notify(f"spawn failed: {exc}", severity="error", timeout=8)
            self._chat.focus()
            return
        agent_id = str(agent_id or "")
        if not agent_id:
            self.notify("spawn failed: missing agent id", severity="error", timeout=6)
            self._chat.focus()
            return
        self._crows.roster_add_rogue(agent_id)
        self._chat_target_agent_id = agent_id
        self._chat_target_label = agent_id
        self._chat_pending_message = None
        self._sync_chat_recipient()
        self._refresh_service_views()
        self._chat.focus()

    def on_chat_input_empty_submit(self, event: ChatInput.EmptySubmit) -> None:
        del event
        target_id = self._chat_target_agent_id
        if target_id is None or not (
            target_id.startswith("crow-") or "rogue-" in target_id
        ):
            return
        self.run_worker(self._interrupt_crow(target_id), exclusive=False, group="chat")

    def on_chat_input_raw_key_press(self, event: ChatInput.RawKeyPress) -> None:
        self.run_worker(
            self._send_raw_key_to_chat_target(event.key, literal=event.literal),
            exclusive=False,
            group="chat",
        )

    def on_chat_input_raw_key_mode_exit(self, event: ChatInput.RawKeyModeExit) -> None:
        del event
        self._set_raw_key_mode(False)

    async def _capture_pane_via_bus(self, session: str, lines: int) -> str:
        return await self.runtime.capture_pane(session, lines=lines)

    def _sync_chat_recipient(self) -> None:
        target = self._chat_target_agent_id
        is_crow = target is not None and not target.startswith("planner-")
        label = self._chat_target_label if target else "collaborator"
        self._chat.set_recipient(label, is_crow=is_crow)
        self._chat.set_pending(self._chat_pending_message if is_crow else None)

    async def _dispatch_chat(self, text: str) -> None:  # noqa: PLR0911, PLR0912
        if text.startswith("!"):
            await self._run_shell_cmd(text[1:].strip())
            return
        if text.startswith(":"):
            await self._handle_colon(text)
            return

        # Capture before any awaits — the periodic refresh can reset
        # _chat_target_agent_id between yields, causing spurious collab routing.
        target_id = self._chat_target_agent_id

        # @-routing: @newplanner / @np scaffolds a fresh plan; @<planname>
        # targets an existing planner.  Body-less @xxx just focuses, no send.
        if text.startswith("@"):
            parts = text.split(None, 1)
            handle = parts[0][1:].lower()
            body = parts[1] if len(parts) > 1 else ""
            if handle in ("newplanner", "np"):
                plan_name = await self._scaffold_new_plan()
                if plan_name is None:
                    self.notify("plan scaffold failed", severity="error", timeout=5)
                    return
                target_id = f"planner-{plan_name}"
                await self._focus_plan(plan_name)
            elif handle == "collaborator":
                target_id = None  # route through the existing collab branch below
                text = body
                if not body:
                    return
            elif _is_ticket_handle(handle, self.runtime.repo_root):
                target_id = f"crow-{handle}"
            else:
                target_id = f"planner-{handle}"
            if not body:
                return
            text = body

        if target_id is not None:
            result = await self._submit_command(
                target_worker="orchestrator",
                kind="agent.message",
                payload={"agent_id": target_id, "message": text},
                timeout_s=COLLABORATOR_START_TIMEOUT_S,
            )
            if result is None:
                return
            if result.get("handled") is False:
                error = str(result.get("error") or "agent did not handle message")
                self.notify(error, severity="error", timeout=6)
                return
            if target_id.startswith("crow-"):
                if result.get("queued"):
                    self._chat_pending_message = text
                    self._sync_chat_recipient()
                    self.notify("message queued (crow busy)", timeout=3)
                else:
                    self._chat_pending_message = None
                    self._sync_chat_recipient()
            if target_id.startswith("planner-"):
                self._sync_planner_mirror_session(target_id[len("planner-"):])
            label = (
                self._chat_target_label
                if target_id == self._chat_target_agent_id
                else target_id
            )
            self.notify(f"→ {label}", timeout=2)
            return

        # Default: collaborator. Lock serializes ensure+send on cold start.
        async with self._collab_lock:
            result = await self._submit_command(
                target_worker="collaborator",
                kind="collaborator.chat_send",
                payload={"text": text},
                timeout_s=COLLABORATOR_START_TIMEOUT_S,
            )
            if result is None:
                return
            self._sync_collaborator_mirror_session()
            self._collab_chat.add_turn("you", text)
            self._collab_chat.add_status("collaborator is thinking…")
        self.notify("→ collaborator", timeout=2)

    async def _interrupt_crow(self, agent_id: str) -> None:
        result = await self._submit_command(
            target_worker="orchestrator",
            kind="agent.interrupt",
            payload={"agent_id": agent_id},
            timeout_s=15.0,
        )
        if result is None:
            return
        if result.get("handled") is False:
            error = str(result.get("error") or "interrupt failed")
            self.notify(error, severity="error", timeout=6)
            return
        self.notify("interrupt sent", timeout=2)

    async def _run_shell_cmd(self, cmd: str) -> None:
        if not cmd:
            return
        try:
            session_name = await self.runtime.run_shell_command(
                cmd,
                prior_session=self._shell_session,
            )
        except Exception as e:
            self.notify(f"shell error: {e}", severity="error", timeout=5)
            return
        self._shell_session = session_name
        self._mirror.set_session(session_name)
        self.run_worker(self._mirror.refresh_pane(), exclusive=True, group="mirror")
        self.notify(f"! {cmd}", timeout=2)

    async def _handle_colon(self, text: str) -> None:
        stripped = text.strip()
        cmd_lower = stripped.lower()
        if cmd_lower in {":wq", ":q", ":q!"}:
            self.exit()
            return
        elif cmd_lower.startswith(":ticket "):
            title = stripped[len(":ticket "):].strip()
            if not title:
                self.notify(":ticket requires a title", severity="warning", timeout=3)
                return
            await self._quick_create_ticket(title)
        elif cmd_lower.startswith(":quick "):
            title = stripped[len(":quick "):].strip()
            if not title:
                self.notify(":quick requires a title", severity="warning", timeout=3)
                return
            await self._quick_kick_ticket(title)
        elif cmd_lower.startswith(":rename "):
            args = stripped.split(maxsplit=2)
            target_id = self._chat_target_agent_id
            if target_id is not None and is_rogue_agent_id(target_id):
                if len(args) != RENAME_SELECTED_ARG_COUNT:
                    self.notify(
                        ":rename <new> while chatting a rogue crow",
                        severity="warning",
                        timeout=3,
                    )
                    return
                new_name = args[1].strip()
                if not new_name:
                    self.notify(":rename requires a new name", severity="warning", timeout=3)
                    return
                await self._rename_rogue_crow(target_id, new_name)
                return
            if len(args) == RENAME_SELECTED_ARG_COUNT:
                old_name = self._plans.selected_name
                new_name = args[1].strip()
            else:
                old_name = args[1].strip()
                new_name = args[2].strip()
            if not old_name:
                self.notify(
                    ":rename requires a selected plan or old name",
                    severity="warning",
                    timeout=3,
                )
                return
            if not new_name:
                self.notify(":rename requires a new name", severity="warning", timeout=3)
                return
            await self._rename_plan(old_name, new_name)
        elif cmd_lower.startswith(":deprecate"):
            parts = stripped.split(maxsplit=1)
            name = parts[1].strip() if len(parts) > 1 else self._plans.selected_name
            if not name:
                self.notify(
                    ":deprecate requires a selected plan or name",
                    severity="warning",
                    timeout=3,
                )
                return
            await self._deprecate_plan(name)
        elif cmd_lower == ":hideescalations":
            self._escalations_visible = not self._escalations_visible
            self._escalations.set_user_visible(self._escalations_visible)
            self.notify(
                f"escalations strip: {'on' if self._escalations_visible else 'off'}",
                timeout=2,
            )
        elif cmd_lower in _COLON_RAW_KEYS:
            await self._send_raw_key_to_chat_target(_COLON_RAW_KEYS[cmd_lower])
        elif cmd_lower == ":raw":
            self._set_raw_key_mode(True)

    def _set_raw_key_mode(self, active: bool) -> None:
        self._raw_key_mode = active
        self._chat.set_raw_key_mode(active)
        if active:
            self.set_focus(self._chat)
            self.notify("raw key mode — Esc Esc to exit", timeout=4)
        else:
            self.notify("raw key mode off", timeout=2)

    def set_focus(self, widget: Widget | None) -> None:
        if self._raw_key_mode:
            widget = self._chat
        super().set_focus(widget)

    async def _send_raw_key_to_chat_target(self, key: str, *, literal: bool = False) -> None:
        result = await self._submit_command(
            target_worker="orchestrator",
            kind="agent.send_key",
            payload={
                "agent_id": self._chat_target_agent_id,
                "key": key,
                "literal": literal,
            },
            timeout_s=15.0,
        )
        if result is None:
            return
        if result.get("handled") is False:
            error = str(result.get("error") or "raw key not delivered")
            self.notify(error, severity="error", timeout=6)
            return
        session = result.get("session")
        if isinstance(session, str) and self._mirror._session == session:
            self.run_worker(self._mirror.refresh_pane(), exclusive=True, group="mirror")
        label = self._chat_target_label or "harness"
        self.notify(f"→ {label}: {key}", timeout=2)

    def action_new_plan_session(self) -> None:
        """ctrl+p: scaffold a new plan and make it the chat target."""
        if self._view != "planning":
            return
        self.run_worker(self._new_plan_session(), exclusive=False, group="chat")

    async def _new_plan_session(self) -> None:
        plan_name = await self._scaffold_new_plan()
        if plan_name is None:
            self.notify("plan scaffold failed", severity="error", timeout=5)
            return
        await self._focus_plan(plan_name)
        self.notify(f"new plan: {plan_name}", timeout=3)

    async def _scaffold_new_plan(self) -> str | None:
        """Create a placeholder plan and return its canonical name."""
        name = "plan-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        result = await self._submit_command(
            target_worker="orchestrator",
            kind="plan.scaffold",
            payload={"name": name, "body": "# Plan Name\n"},
            timeout_s=10.0,
        )
        if result is None:
            return None
        return str(result.get("name") or name)

    async def _focus_plan(self, name: str) -> None:
        self._refresh_service_views()
        plan_names = list(getattr(self._plans, "_plans", []))
        if name in plan_names:
            self._plans.move_cursor(row=plan_names.index(name))
        self._active_document = "plan"
        self._has_selected_plan = True
        self._plan_doc.display = True
        self._notes_doc.display = False
        self._report_doc.display = False
        self._chat_target_agent_id = f"planner-{name}"
        self._chat_target_label = f"planner: {name}"
        self._sync_chat_recipient()
        self._sync_planner_mirror_session(name)
        await self._render_plan(name)

    async def _rename_plan(self, old_name: str, new_name: str) -> None:
        result = await self._submit_command(
            target_worker="orchestrator",
            kind="plan.rename",
            payload={"old_name": old_name, "new_name": new_name},
            timeout_s=10.0,
        )
        if result is None:
            return
        name = str(result.get("name") or new_name)
        await self._focus_plan(name)
        self.notify(f"renamed plan: {old_name} → {name}", timeout=3)

    async def _rename_rogue_crow(self, old_agent_id: str, new_name: str) -> None:
        result = await self._submit_command(
            target_worker="orchestrator",
            kind="crow.rename_rogue",
            payload={"agent_id": old_agent_id, "name": new_name},
            timeout_s=15.0,
        )
        if result is None:
            return
        if result.get("handled") is False:
            error = str(result.get("error") or "rename failed")
            self.notify(error, severity="error", timeout=6)
            return
        new_agent_id = str(result.get("agent_id") or old_agent_id)
        if new_agent_id != old_agent_id:
            self._crows.roster_rename_rogue(old_agent_id, new_agent_id)
            if self._chat_target_agent_id == old_agent_id:
                self._chat_target_agent_id = new_agent_id
                self._chat_target_label = new_agent_id
                self._chat_pending_message = None
                self._sync_chat_recipient()
        self.notify(f"renamed rogue: {old_agent_id} → {new_agent_id}", timeout=3)
        self._refresh_service_views()

    def action_open_note_capture(self) -> None:
        screen = NoteCaptureScreen(
            initial_draft=self._note_capture_draft,
            load_recent_rows=self._sync_recent_note_entries,
        )
        self.push_screen(screen, self._on_note_capture_closed)

    async def _sync_recent_note_entries(self) -> list[dict]:
        return await self.runtime.get_notetaker_recent_entries(RECENT_NOTE_ROWS)

    async def _slash_note_submit(self, body: str) -> None:
        body = body.strip()
        if not body:
            return
        self.run_worker(
            self._capture_note_via_service(body),
            exclusive=False,
            group="note_capture",
        )

    def _on_note_capture_closed(self, payload: tuple[bool, str] | None) -> None:
        if payload is None:
            return
        submitted, draft_snapshot = payload
        self._note_capture_draft = "" if submitted else draft_snapshot
        if submitted:
            body = draft_snapshot.strip()
            if body:
                self.run_worker(
                    self._capture_note_via_service(body),
                    exclusive=False,
                    group="note_capture",
                )

    async def _capture_note_via_service(self, raw: str) -> None:
        """Submit note capture through the service command; toast on completion."""
        result = await self._submit_command(
            target_worker="orchestrator",
            kind="notetaker.capture.submit",
            payload={"raw": raw},
            timeout_s=60.0,
            notify_errors=True,
        )
        if result is None:
            return
        note_name = str(result.get("note_name") or "")
        short_vers = str(result.get("short_vers") or "")
        self._refresh_service_views()
        if note_name:
            self.notify(short_vers or f"note saved: {note_name}", timeout=5)
        else:
            self.notify(short_vers or "note saved", timeout=5)

    async def _quick_create_ticket(self, title: str) -> None:
        """Write a .murder/tickets/<id>.md; TicketSync imports it as PLANNED."""
        try:
            ticket_id = _next_ticket_id(self.runtime.repo_root)
            path = ticket_md(self.runtime.repo_root, ticket_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"# {title}\n\n## Plan\n\n## Working Notes\n")
        except Exception as exc:
            self.notify(f"ticket create failed: {exc}", severity="error", timeout=6)
            return
        self.notify(f"ticket {ticket_id}: {title}", timeout=5)

    async def _quick_kick_ticket(self, title: str) -> None:
        """Create a ticket and immediately kick it via the service."""
        result = await self._submit_command(
            target_worker="orchestrator",
            kind="ticket.quick_kick",
            payload={"title": title},
            timeout_s=30.0,
            notify_errors=True,
        )
        if result is None:
            return
        ticket_id = str(result.get("ticket_id") or "")
        kicked = list(result.get("kicked") or [])
        if kicked:
            self.notify(f"kicked {ticket_id}: {title}", timeout=5)
        else:
            self.notify(f"created {ticket_id}: {title} (not yet ready)", timeout=5)
        self._refresh_service_views()

    def action_focus_chat(self) -> None:
        if self._raw_key_mode:
            self.set_focus(self._chat)
            return
        if self.focused is not self._chat:
            self._pre_chat_focus = self.focused
        self.set_focus(self._chat)

    def action_quick_spawn(self) -> None:
        self._set_view("crows")
        self.set_focus(self._chat)
        self._chat.post_message(ChatInput.SpawnCommand())

    def action_restore_focus(self) -> None:
        if self._raw_key_mode:
            return
        target = self._pre_chat_focus
        self._pre_chat_focus = None
        if target is not None and self._is_displayed(target):
            self._focus_target(target)
            return
        # Fallback: focus current view's primary widget
        if self._view == "planning":
            if self._sidebar_visible:
                if self._active_document == "note":
                    self.set_focus(self._notes_list)
                elif self._active_document == "report":
                    self.set_focus(self._reports_list)
                else:
                    self.set_focus(self._plans)
            else:
                self._focus_planning_after_sidebar_hide()
        elif self._view == "crows":
            if not self._crows.focus_last_tile() and not self._crows.focus_first_tile():
                self.set_focus(self._crows)
        else:
            self.set_focus(None)

    def action_show_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_show_help_force(self) -> None:
        self.push_screen(HelpScreen())

    # ── pane focus traversal (VISION §4.3) ─────────────────────────────────
    # ctrl+hjkl / ctrl+arrows move focus between panes by screen direction.
    # Bare hjkl/arrows are *not* bound here, so the focused widget keeps its
    # intra-pane motion.

    def _focus_contains(self, pane: Widget) -> bool:
        current = self.focused
        return current is pane or (current is not None and current in pane.walk_children())

    def _is_displayed(self, widget: Widget | None) -> bool:
        if widget is None:
            return False
        node: Widget | None = widget
        while node is not None:
            if not node.display:
                return False
            parent = node.parent
            node = parent if isinstance(parent, Widget) else None
        return True

    def _focus_target(self, target: Widget | None) -> bool:
        if not self._is_displayed(target):
            return False
        assert target is not None
        if target is self._crows:
            if self._crows.enlarged_agent_id is not None:
                with contextlib.suppress(Exception):
                    self.set_focus(self._crows.query_one(PaneMirror))
                    return True
            if self._crows.focus_last_tile() or self._crows.focus_first_tile():
                return True
            if self._crows.focus_roster():
                return True
        elif target is self._crows.wall:
            if self._crows.focus_last_tile() or self._crows.focus_first_tile():
                return True
            self.set_focus(target)
            return True
        elif target is self._crows.roster:
            if self._crows.focus_roster():
                return True
            self.set_focus(target)
            return True
        self.set_focus(target)
        return True

    def _focus_planning_after_sidebar_hide(self) -> None:
        self._focus_target(
            self._planning_document_pane()
            or self._planning_right_pane()
            or (self._chat if self._chat.display else None)
        )

    def _planning_document_pane(self) -> Widget | None:
        active_doc = {
            "report": self._report_doc,
            "note": self._notes_doc,
            "plan": self._plan_doc,
        }.get(self._active_document)
        if active_doc is not None and self._is_displayed(active_doc):
            return active_doc
        for candidate in (self._report_doc, self._notes_doc, self._plan_doc):
            if self._is_displayed(candidate):
                return candidate
        return None

    def _planning_right_pane(self) -> Widget | None:
        if self._is_displayed(self._collab_chat):
            return self._collab_chat
        if self._is_displayed(self._mirror):
            return self._mirror
        return None

    def _bottom_focus_pane(self) -> Widget | None:
        if self._is_displayed(self._escalations):
            return self._escalations
        if self._is_displayed(self._chat):
            return self._chat
        return None

    def _shift_planning_focus(self, direction: str) -> bool:  # noqa: PLR0912
        doc = self._planning_document_pane()
        right = self._planning_right_pane()
        bottom = self._bottom_focus_pane()
        esc = self._escalations if self._is_displayed(self._escalations) else None
        chat = self._chat if self._is_displayed(self._chat) else None

        target: Widget | None = None
        if direction == "right":
            if (
                self._is_displayed(self._plans)
                and self._focus_contains(self._plans)
            ) or (
                self._is_displayed(self._notes_list)
                and self._focus_contains(self._notes_list)
            ) or (
                self._is_displayed(self._reports_list)
                and self._focus_contains(self._reports_list)
            ):
                target = doc or right or chat
            elif doc is not None and self._focus_contains(doc):
                target = right or chat
        elif direction == "left":
            if right is not None and self._focus_contains(right):
                target = doc or (
                    self._reports_list if self._is_displayed(self._reports_list) else None
                )
            elif doc is not None and self._focus_contains(doc):
                if self._active_document == "report":
                    target = (
                        self._reports_list if self._is_displayed(self._reports_list) else None
                    )
                elif self._active_document == "note":
                    target = self._notes_list if self._is_displayed(self._notes_list) else None
                else:
                    target = self._plans if self._is_displayed(self._plans) else None
        elif direction == "down":
            if self._is_displayed(self._plans) and self._focus_contains(self._plans):
                target = self._notes_list
            elif self._is_displayed(self._notes_list) and self._focus_contains(self._notes_list):
                target = self._reports_list
            elif (
                (
                    self._is_displayed(self._reports_list)
                    and self._focus_contains(self._reports_list)
                )
                or (doc is not None and self._focus_contains(doc))
                or (right is not None and self._focus_contains(right))
            ):
                target = bottom or chat
            elif esc is not None and self._focus_contains(esc):
                target = chat
        elif direction == "up":
            sidebar_target = (
                self._reports_list if self._is_displayed(self._reports_list) else None
            )
            if chat is not None and self._focus_contains(chat):
                target = esc or sidebar_target or doc or right
            elif esc is not None and self._focus_contains(esc):
                target = sidebar_target or doc or right
            elif self._is_displayed(self._reports_list) and self._focus_contains(
                self._reports_list
            ):
                target = self._notes_list
            elif self._is_displayed(self._notes_list) and self._focus_contains(self._notes_list):
                target = self._plans

        return self._focus_target(target)

    def _shift_crows_focus(self, direction: str) -> bool:
        esc = self._escalations if self._is_displayed(self._escalations) else None
        chat = self._chat if self._is_displayed(self._chat) else None
        roster = self._crows.roster
        wall = self._crows.wall
        wall_visible = self._is_displayed(wall)
        target: Widget | None = None

        if direction == "right":
            if self._focus_contains(roster):
                target = wall if wall_visible else esc or chat
            elif wall_visible and self._focus_contains(wall):
                target = esc or chat
            elif esc is not None and self._focus_contains(esc):
                target = chat
        elif direction == "left":
            if chat is not None and self._focus_contains(chat):
                target = esc or (wall if wall_visible else roster)
            elif esc is not None and self._focus_contains(esc):
                target = wall if wall_visible else roster
            elif wall_visible and self._focus_contains(wall):
                target = roster
        elif direction == "down":
            if self._focus_contains(roster) or (wall_visible and self._focus_contains(wall)):
                target = esc or chat
            elif esc is not None and self._focus_contains(esc):
                target = chat
        elif direction == "up":
            if chat is not None and self._focus_contains(chat):
                target = esc or (wall if wall_visible else roster)
            elif esc is not None and self._focus_contains(esc):
                target = wall if wall_visible else roster

        return self._focus_target(target)

    def _dispatch_widget(self, widget_type: type[Widget]) -> Widget | None:
        try:
            widget = self._dispatch.query_one(widget_type)
        except Exception:
            return None
        return widget if self._is_displayed(widget) else None

    def _shift_dispatch_focus(self, direction: str) -> bool:  # noqa: PLR0912
        mode = self._dispatch_widget(ModeStrip)
        gauges = self._dispatch_widget(GaugeStrip)
        tickets = self._dispatch_widget(ScheduleTicketsTable)
        calendar = self._dispatch_widget(CalendarPanel)
        esc = self._escalations if self._is_displayed(self._escalations) else None
        target: Widget | None = None

        if direction == "right":
            if tickets is not None and self._focus_contains(tickets):
                target = calendar
        elif direction == "left":
            if calendar is not None and self._focus_contains(calendar):
                target = tickets
        elif direction == "down":
            if mode is not None and self._focus_contains(mode):
                target = gauges or tickets
            elif gauges is not None and self._focus_contains(gauges):
                target = tickets
            elif (
                (tickets is not None and self._focus_contains(tickets))
                or (calendar is not None and self._focus_contains(calendar))
            ):
                target = esc
        elif direction == "up":
            if esc is not None and self._focus_contains(esc):
                target = tickets or calendar
            elif tickets is not None and self._focus_contains(tickets):
                target = gauges or mode
            elif calendar is not None and self._focus_contains(calendar):
                target = gauges or mode
            elif gauges is not None and self._focus_contains(gauges):
                target = mode

        return self._focus_target(target)

    def _shift_focus_direction(self, direction: str) -> None:
        if self._view == "planning" and self._shift_planning_focus(direction):
            return
        if self._view == "crows" and self._shift_crows_focus(direction):
            return
        if self._view == "schedule" and self._shift_dispatch_focus(direction):
            return

    def _ordered_focusable_panes(self) -> list[Widget]:
        """Top-level panes currently visible in the active view, in tab order."""
        if self._view == "planning":
            candidates = [
                self._plans,
                self._notes_list,
                self._reports_list,
                self._plan_doc,
                self._notes_doc,
                self._report_doc,
                self._collab_chat,
                self._mirror,
                self._escalations,
                self._chat,
            ]
        elif self._view == "crows":
            candidates = [
                self._crows.roster,
                self._crows.wall,
                self._escalations,
                self._chat,
            ]
        else:  # schedule/dispatch
            candidates = [
                self._dispatch_widget(ModeStrip),
                self._dispatch_widget(GaugeStrip),
                self._dispatch_widget(ScheduleTicketsTable),
                self._dispatch_widget(CalendarPanel),
                self._escalations,
            ]
        return [w for w in candidates if w is not None and self._is_displayed(w)]

    def _shift_focus(self, delta: int) -> None:
        panes = self._ordered_focusable_panes()
        if not panes:
            return
        idx = -1
        for i, pane in enumerate(panes):
            if self._focus_contains(pane):
                idx = i
                break
        if idx >= 0:
            target_idx = (idx + delta) % len(panes)
        else:
            target_idx = 0 if delta > 0 else len(panes) - 1
        target = panes[target_idx]
        self._focus_target(target)

    def action_focus_next_region(self) -> None:
        self._shift_focus(1)

    def action_focus_previous_region(self) -> None:
        self._shift_focus(-1)

    def action_focus_right(self) -> None:
        if self._cycle_chat_target_if_focused(1):
            return
        self._shift_focus_direction("right")

    def action_focus_left(self) -> None:
        if self._cycle_chat_target_if_focused(-1):
            return
        self._shift_focus_direction("left")

    def action_focus_down(self) -> None:
        self._shift_focus_direction("down")

    def action_focus_up(self) -> None:
        self._shift_focus_direction("up")

    def action_kick_ready(self) -> None:
        self.run_worker(self._kick_ready(), exclusive=False)

    async def _kick_ready(self) -> None:
        await self._dispatch_ctrl.kick_ready()

    def action_schedule_mode_picker(self) -> None:
        if self._view != "schedule":
            return
        strip = self._dispatch.query_one(ModeStrip)
        strip.action_open_mode_picker()
        strip.focus()

    def action_schedule_apply_carve(self) -> None:
        if self._insert_if_chat_focused("c"):
            return
        if self._view != "schedule":
            return
        tid = self._dispatch.selected_ticket_id
        if not tid:
            self.notify("Select a ticket row first", severity="warning", timeout=4)
            return
        self._open_carve_screen(tid)

    def on_schedule_tickets_table_carve_requested(
        self, event: ScheduleTicketsTable.CarveRequested
    ) -> None:
        event.stop()
        if self._view != "schedule":
            return
        self._open_carve_screen(event.ticket_id)

    def on_schedule_tickets_table_retry_requested(
        self, event: ScheduleTicketsTable.RetryRequested
    ) -> None:
        event.stop()
        self.run_worker(
            self._submit_retry_failed(event.ticket_id),
            exclusive=False,
            group="retry",
        )

    def on_escalation_strip_ack_requested(self, event: EscalationStrip.AckRequested) -> None:
        event.stop()
        self._launch_escalation_wizard(event.escalation)

    def _launch_escalation_wizard(self, escalation: EscalationSummary) -> None:
        if self._escalation_wizard is not None:
            self._escalation_wizard.focus()
            return
        if self._spawn_wizard is not None:
            self._spawn_wizard.focus()
            return
        parent = self._chat.parent
        if parent is None:
            self.notify("escalation resolver unavailable", severity="error", timeout=4)
            return
        self._chat.display = False
        wizard = EscalationResolveWizard(escalation)
        self._escalation_wizard = wizard
        parent.mount(wizard, before=self._chat)
        wizard.focus()

    def on_escalation_resolve_wizard_confirmed(
        self, event: EscalationResolveWizard.Confirmed
    ) -> None:
        event.stop()
        if event.action == "ack":
            self._teardown_escalation_wizard(focus_chat=False)
            self.run_worker(
                self._ack_escalation(event.escalation.id),
                exclusive=False,
                group="escalation_ack",
            )
            return
        if event.action == "retry_ack" and event.escalation.ticket_id:
            self._teardown_escalation_wizard(focus_chat=False)
            self.run_worker(
                self._retry_and_ack_escalation(event.escalation.ticket_id, event.escalation.id),
                exclusive=False,
                group="escalation_retry_ack",
            )
            return
        self._teardown_escalation_wizard()
        if event.action == "navigate":
            self._navigate_to_escalation(event.escalation)

    def on_escalation_resolve_wizard_cancelled(
        self, event: EscalationResolveWizard.Cancelled
    ) -> None:
        event.stop()
        self._teardown_escalation_wizard()

    def _teardown_escalation_wizard(self, *, focus_chat: bool = True) -> None:
        wizard = self._escalation_wizard
        if wizard is not None:
            wizard.remove()
            self._escalation_wizard = None
        self._chat.display = self._view != "schedule" and self._spawn_wizard is None
        if focus_chat and self._chat.display:
            self._chat.focus()

    def _focus_bottom_pane(self) -> None:
        """Focus escalations when active, otherwise chat (if visible)."""
        self._focus_target(self._bottom_focus_pane())

    async def _retry_and_ack_escalation(self, ticket_id: str, escalation_id: int) -> None:
        await self._submit_retry_failed(ticket_id)
        await self.runtime.ack_escalation(escalation_id)
        self.notify("Ticket retry queued; escalation acknowledged.", timeout=3)
        await self._refresh_bus_views()
        self._focus_bottom_pane()

    def _navigate_to_escalation(self, esc: EscalationSummary) -> None:
        if esc.to_recipient == "collaborator":
            self._set_view("planning")
        elif esc.ticket_id:
            self._set_view("crows")
        else:
            self.notify(
                f"Escalation #{esc.id}: {esc.reason}",
                severity="warning",
                timeout=8,
            )

    async def _ack_escalation(self, escalation_id: int) -> None:
        await self.runtime.ack_escalation(escalation_id)
        self.notify("Escalation acknowledged.", timeout=3)
        await self._refresh_bus_views()
        self._focus_bottom_pane()

    def on_escalation_strip_retry_requested(self, event: EscalationStrip.RetryRequested) -> None:
        event.stop()
        self.run_worker(
            self._submit_retry_failed(event.ticket_id),
            exclusive=False,
            group="retry",
        )

    def on_escalation_strip_navigate_requested(
        self, event: EscalationStrip.NavigateRequested
    ) -> None:
        event.stop()
        self._navigate_to_escalation(event.escalation)

    def on_mode_strip_set_mode_requested(self, event: ModeStrip.SetModeRequested) -> None:
        event.stop()
        self.run_worker(
            self._submit_set_scheduler_mode(event.to_mode),
            exclusive=False,
            group="scheduler",
        )

    async def _submit_set_scheduler_mode(self, to_mode: str) -> None:
        await self._dispatch_ctrl.set_scheduler_mode(to_mode)

    def _open_carve_screen(self, ticket_id: str) -> None:
        self.run_worker(
            self._dispatch_ctrl.open_carve_screen(ticket_id),
            exclusive=True,
            group="carve_open",
        )

    def _enqueue_carve_autosave(self, ticket_id: str, spec: dict[str, object]) -> None:
        self._dispatch_ctrl.enqueue_carve_autosave(ticket_id, spec)

    async def _submit_retry_failed(self, ticket_id: str) -> None:
        await self._dispatch_ctrl.retry_failed(ticket_id)

    async def _submit_update_metadata_and_status(
        self,
        ticket_id: str,
        spec: dict[str, object],
        *,
        notify_success: bool = True,
    ) -> None:
        await self._dispatch_ctrl.update_metadata_and_status(
            ticket_id, spec, notify_success=notify_success
        )

    def _record_ui_escalation(self, reason: str) -> None:
        self.run_worker(
            self._submit_command(
                target_worker="state",
                kind="state.escalation.create",
                payload={
                    "ticket_id": None,
                    "severity": 2,
                    "reason": reason,
                    "to_recipient": "user",
                },
                timeout_s=10.0,
                notify_errors=False,
            ),
            exclusive=False,
            group="ui_escalation",
        )

    def _insert_if_chat_focused(self, text: str) -> bool:
        if self.focused is not self._chat:
            return False
        self._chat.insert(text)
        return True

    def _chat_target_cycle_enabled(self) -> bool:
        return (
            self.focused is self._chat
            and not self._raw_key_mode
            and self._view in ("planning", "crows")
        )

    def _chat_target_options(self) -> list[ChatTarget]:
        if self._view == "planning":
            return planning_chat_targets(self._crow_snapshot)
        wall_order, entries = self._crows.visible_wall_chat_targets()
        return crows_chat_targets(wall_order, entries)

    def _apply_chat_target(self, target: ChatTarget) -> None:
        if target.agent_id != self._chat_target_agent_id:
            self._chat_pending_message = None
        self._chat_target_agent_id = target.agent_id
        self._chat_target_label = target.label
        if self._view == "planning":
            # Mirror vs parsed collab chat follows planner target; keep panes in sync.
            self._apply_mode()
        elif self._view == "crows":
            self._sync_chat_recipient()
            if target.agent_id is not None:
                _, entries = self._crows.visible_wall_chat_targets()
                crow_entry = entries.get(target.agent_id)
                if crow_entry is not None and crow_entry.session:
                    self._mirror.set_session(crow_entry.session)
        else:
            self._sync_chat_recipient()

    def _cycle_chat_target_if_focused(self, delta: int) -> bool:
        if not self._chat_target_cycle_enabled():
            return False
        options = self._chat_target_options()
        next_target = cycle_chat_target(options, self._chat_target_agent_id, delta)
        if next_target is None:
            return True
        self._apply_chat_target(next_target)
        return True

    def action_open_settings(self) -> None:
        screen = SettingsScreen(
            config=self.runtime.config,
            repo=self.runtime.repo_root,
            user_config=self._user_config,
            available_themes=sorted(self.available_themes),
            settings_service=SettingsService(self.runtime.repo_root),
        )
        self.push_screen(screen, self._on_settings_closed)

    def _on_settings_closed(self, saved: bool) -> None:
        if not saved:
            return
        try:
            self.runtime.config = Config.load(self.runtime.repo_root)
        except Exception as exc:
            self.notify(
                f"Settings saved, but failed to reload project config: {exc}",
                severity="error",
                timeout=6,
            )
        self._user_config = load_user_config()
        if self._user_config.tui.theme and self._user_config.tui.theme in self.available_themes:
            self.theme = self._user_config.tui.theme
        self._header.project = self.runtime.config.project.name
        self._header._update_text()
        self.notify("Settings saved.", timeout=3)

    async def _submit_command(
        self,
        *,
        target_worker: str,
        kind: str,
        payload: dict[str, object],
        timeout_s: float,
        notify_errors: bool = True,
    ) -> dict[str, object] | None:
        submit_command = getattr(self.runtime, "submit_command", None)
        if submit_command is None:
            if notify_errors:
                self.notify("service client unavailable", severity="error", timeout=4)
            return None
        try:
            return await submit_command(
                target_worker=target_worker,
                kind=kind,
                payload=payload,
                timeout_s=timeout_s,
            )
        except Exception as exc:
            if notify_errors:
                self.notify(str(exc), severity="error", timeout=8)
            return None

    def _sync_collaborator_mirror_session(self) -> None:
        snapshot = self._crow_snapshot
        if snapshot is None:
            return
        for session in snapshot.sessions:
            if session.role == "collaborator" and session.status in ("running", "idle"):
                if session.session_name:
                    self._mirror.set_session(session.session_name)
                    self.run_worker(self._mirror.refresh_pane(), exclusive=True, group="mirror")
                return

    def _planner_target_name(self) -> str | None:
        target = self._chat_target_agent_id
        if target is None or not target.startswith("planner-"):
            return None
        plan_name = target[len("planner-") :]
        return plan_name or None

    def _sync_planner_mirror_session(self, plan_name: str) -> None:
        if self._view != "planning":
            return
        session_name = format_session_name(self.runtime, "planner", f"_{plan_name}")
        self._mirror.set_session(session_name)
        self._mirror.border_title = f"planner: {plan_name}"
        self.run_worker(self._mirror.refresh_pane(), exclusive=True, group="mirror")

    def _crow_session_for_ticket(self, ticket_id: str) -> str | None:
        snapshot = self._crow_snapshot
        if snapshot is None:
            return None
        for session in snapshot.sessions:
            if (
                session.ticket_id == ticket_id
                and session.role == "crow"
                and session.status in ("running", "idle")
            ):
                return session.session_name
        return None
