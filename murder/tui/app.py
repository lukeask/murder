"""Top-level Textual app — wires header, ticket grid, pane mirror, and
escalation strip onto the running service client."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Footer, Static

from murder.terminal import tmux
from murder.config import Config
from murder.tui.chat_input import ChatInput
from murder.tui.dispatch import DispatchView, ScheduleTicketsTable
from murder.tui.dispatch.mode_strip import ModeStrip
from murder.tui.note_capture import RECENT_NOTE_ROWS, NoteCaptureScreen
from murder.tui.pane_mirror import PaneMirror
from murder.tui.perf_log import make_perf_log
from murder.tui.settings_screen import SettingsScreen
from murder.tui.themes import CUSTOM_THEMES

from murder.tui.controllers import DispatchController, TuiContext
from murder.tui.crows_view import CrowsView, CrowTile
from murder.tui.escalation_strip import EscalationStrip
from murder.tui.header import Header
from murder.tui.planning_mode_widgets import (
    ChatLog,
    NotesDocument,
    NotesList,
    PlanDocument,
    PlanList,
)
from murder.tui.ticket_grid import TicketGrid
from murder.service.settings_service import SettingsService
from murder.user_config import UserConfig, load_user_config

if TYPE_CHECKING:
    from typing import Any

    from textual.widget import Widget


COLLABORATOR_START_TIMEOUT_S = 120.0
CTRL_C_DOUBLE_TAP_S = 1.5

_VIM_QUIT_COMMANDS = frozenset({":wq", ":q!"})


def _is_vim_style_quit(text: str) -> bool:
    """True only when the *entire* submission is :wq or :q! (case-insensitive).

    Leading/trailing whitespace may surround the command; embedding :wq inside
    normal prose must not quit.
    """

    stripped = text.strip().lower()
    return stripped in _VIM_QUIT_COMMANDS


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
                        "sentinel: handles questions and escalations",
                        "ticket: scoped unit of work with deps, write_set, checklist",
                        "wave: tickets that may run after earlier dependencies finish",
                        "",
                        "[b]slash commands[/b]",
                        "/murder  kick ready tickets",
                        "/note    quick capture (bare = overlay; with text = submit)",
                        "/exit    quit murder",
                        "!<cmd>   run shell command (output in pane mirror)",
                        ":wq :q!  vim-style quit",
                        "",
                        "[b]keys[/b]",
                        "ctrl+f focus chat · ? help · ctrl+, settings",
                        "ctrl+1/2/3  switch views  ·  [ and ] cycle views",
                        "Dispatch: [b]c[/b] / Enter opens ticket metadata editor; "
                        "F6 kicks ready rows",
                        "ctrl+b  toggle docs sidebar",
                        "ctrl+y  collaborator: parsed chat ⇄ raw tmux pane",
                        "ctrl+c twice  force quit  ·  escape  unfocus chat",
                        "j/k or ↑/↓  vim-style navigation in lists and logs",
                        "ctrl+r refresh · ctrl+u refresh usage",
                        "ctrl+n  quick note capture overlay (global)",
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
        Binding("ctrl+1", "view_planning", "Planning", priority=True),
        Binding("ctrl+2", "view_crows", "Crows", priority=True),
        Binding("ctrl+3", "view_schedule", "Dispatch", priority=True),
        Binding("[", "previous_view", "Prev view", priority=True),
        Binding("]", "next_view", "Next view", priority=True),
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
        ("ctrl+r", "refresh_now", "Refresh"),
        ("ctrl+u", "collect_usage", "Usage"),
        ("c", "schedule_apply_carve", "Metadata"),
        Binding("m", "schedule_mode_picker", "Mode", show=False),
        ("f6", "kick_ready", "Kick"),
        ("ctrl+f", "focus_chat", "Chat"),
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
        border: solid $border;
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
        self._crows = CrowsView(perf_log=self.perf)
        self._plans = PlanList()
        self._plan_doc = PlanDocument()
        self._notes_list = NotesList()
        self._notes_doc = NotesDocument()
        self._collab_chat = ChatLog(agent_label="collaborator")
        self._dispatch = DispatchView()
        self._mirror = PaneMirror(perf=self.perf)
        self._escalations = EscalationStrip()
        self._chat = ChatInput()
        self._collab_lock = asyncio.Lock()
        self._collab_chat_lock = asyncio.Lock()
        self._sidebar_visible = True
        self._collab_raw = False  # ctrl+y: show the raw tmux pane instead of the parsed chat
        self._user_config: UserConfig = load_user_config()
        self._view = "planning"
        self._pre_chat_focus = None
        self._has_selected_plan = False
        self._active_document = "plan"
        self._shell_session: str | None = None
        self._last_ctrl_c: float = 0.0
        self._note_capture_draft = ""
        self._crow_snapshot = None
        self._dispatch_ctrl = DispatchController(
            TuiContext(
                submit_command=self._submit_command,
                notify=self.notify,
                refresh_views=self._refresh_db_views,
                push_screen=self.push_screen,
                run_worker=self.run_worker,
                read_model=runtime.read_model,
            )
        )
        for theme in CUSTOM_THEMES:
            self.register_theme(theme)

    def compose(self) -> ComposeResult:
        yield self._header
        with Horizontal(id="body"):
            yield self._grid
            yield self._crows
            with Vertical(id="planning_sidebar"):
                yield self._plans
                yield self._notes_list
            yield self._plan_doc
            yield self._notes_doc
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
        self._refresh_db_views()
        self.set_focus(self._chat)
        if self.runtime.config.project.name == "TODO_SET_ME":
            self.notify(
                "Project name is unset — open Settings (ctrl+,) to update roles.yaml.",
                severity="warning",
                timeout=10,
            )
        interval_s = max(self.runtime.config.tui.refresh_ms, 250) / 1000
        self.set_interval(interval_s, self._refresh_db_views)
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
        self._refresh_db_views()
        self.run_worker(self._mirror.refresh_pane(), exclusive=True, group="mirror")

    def action_collect_usage(self) -> None:
        if self._insert_if_chat_focused("u"):
            return
        self.run_worker(self._collect_usage_snapshots(), exclusive=True, group="usage")

    async def _collect_usage_snapshots(self) -> None:
        await self._dispatch_ctrl.collect_usage_snapshots()

    def _refresh_db_views(self) -> None:
        perf = self.perf
        with perf.span("tui.refresh_db_views"):
            dispatch = self.runtime.read_model.get_dispatch_snapshot()
            with perf.span("tui.header.refresh_counts"):
                self._header.refresh_from_snapshot(dispatch)
            with perf.span("tui.grid.refresh"):
                self._grid.refresh_from_snapshot(dispatch)
            with perf.span("tui.crows.render_snapshot"):
                self._crow_snapshot = self.runtime.read_model.get_crow_snapshot()
                self._crows.render_from_snapshot(self._crow_snapshot)
            with perf.span("tui.plans.refresh"):
                self._plans.refresh_from_snapshot(self.runtime.read_model.get_plans_snapshot())
            with perf.span("tui.notes_list.refresh"):
                self._notes_list.refresh_from_snapshot(
                    self.runtime.read_model.get_notes_snapshot()
                )
            with perf.span("tui.schedule.refresh"):
                self._dispatch.refresh_from_snapshot(
                    self.runtime.read_model.get_schedule_snapshot(),
                    read_model=self.runtime.read_model,
                )
            with perf.span("tui.escalations.refresh"):
                self._escalations.refresh_from_snapshot(
                    self.runtime.read_model.get_escalations_snapshot()
                )
            if (
                self._view == "planning"
                and self._active_document == "plan"
                and self._plans.selected_name
            ):
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

    async def _refresh_pane(self) -> None:
        with self.perf.span("tui.refresh_pane"):
            await self._mirror.refresh_pane()
            if self._view == "crows":
                await self._crows.refresh_tails()
            if self._view == "planning" and not self._collab_raw:
                await self._refresh_collab_chat()

    def on_ticket_grid_ticket_selected(self, event: TicketGrid.TicketSelected) -> None:
        self._mirror.set_session(self._crow_session_for_ticket(event.ticket_id))
        self.run_worker(self._mirror.refresh_pane(), exclusive=True, group="mirror")

    def on_crows_view_tile_selected(self, event: CrowsView.TileSelected) -> None:
        # Keep the shared pane mirror in sync so planning's collab-raw
        # toggle and the shell session share a hint.
        self._mirror.set_session(event.entry.session)

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
                self._plan_doc.display = True
            self._plan_doc.display = True
            self._notes_doc.display = False
            self.run_worker(
                self._render_plan(event.name),
                exclusive=True,
                group="plandoc",
                exit_on_error=False,
            )

    async def on_plan_list_plan_opened(self, event: PlanList.PlanOpened) -> None:
        await self._open_plan(event.name)

    def on_notes_list_note_highlighted(self, event: NotesList.NoteHighlighted) -> None:
        if self._view == "planning":
            self._active_document = "note"
            self._plan_doc.display = False
            self._notes_doc.display = True
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

    async def _render_plan(self, name: str) -> None:
        with self.perf.span("tui.render_plan"):
            display = self.runtime.read_model.get_plan_display(name)
            if display is None:
                return
            await self._plan_doc.set_plan_markdown(name, display.markdown)

    async def _open_plan(self, name: str) -> None:
        await self.runtime.reconcile_plan(name)
        path = self.runtime.plan_path_for(name)
        with self.suspend():
            code = self.runtime.open_editor_blocking(path, self._user_config.tui.editor)
        if code != 0:
            self.notify(f"editor exited with {code}", severity="warning", timeout=5)
        await self.runtime.reconcile_plan(name)
        self._refresh_db_views()
        await self._render_plan(name)

    async def _render_note(self, name: str) -> None:
        with self.perf.span("tui.render_note"):
            display = self.runtime.read_model.get_note_display(name)
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
            else self.runtime.note_path_for(name)
        )
        with self.suspend():
            code = self.runtime.open_editor_blocking(path, self._user_config.tui.editor)
        if code != 0:
            self.notify(f"editor exited with {code}", severity="warning", timeout=5)
        if self.runtime.note_sync is not None:
            await self.runtime.note_sync.reconcile_file(path)
        self._refresh_db_views()
        await self._render_note(name)
        self.set_focus(self._notes_doc)

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
        self._refresh_db_views()
        self.notify(f"retired note: {dest_name}", timeout=4)
        if self._notes_list.selected_name:
            await self._render_note(self._notes_list.selected_name)
        else:
            self._active_document = "plan"
            self._apply_mode()

    async def _refresh_collab_chat(self) -> None:
        """Re-parse the collaborator's tmux pane into the chat transcript.

        Lock-guarded so overlapping refresh-pane ticks don't race two parses
        onto the same conversation log.
        """
        if self._collab_chat_lock.locked():
            return
        async with self._collab_chat_lock:
            with self.perf.span("tui.collab_chat.refresh"):
                if self._view != "planning":
                    return
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
                    self._collab_chat.set_turns([])
                    self._collab_chat.add_status(
                        "(no collaborator yet — type a message to start one)"
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
                self._collab_chat.set_turns([])
                if bool(result.get("has_parser")):
                    self._collab_chat.add_status("(collaborator chat — nothing parsed yet)")
                else:
                    harness_kind = str(result.get("harness_kind", "unknown"))
                    self._collab_chat.add_status(
                        f"(no transcript parser for '{harness_kind}' yet — "
                        "press ctrl+y for the raw pane)"
                    )

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
        self._apply_mode()
        self._refresh_db_views()  # also re-renders the planning doc when a plan is selected

    def _apply_mode(self) -> None:
        planning = self._view == "planning"
        collab_chat_on = planning and not self._collab_raw
        collab_raw_on = planning and self._collab_raw
        self._chat.set_recipient("collaborator")
        self._header.set_view(self._view)
        self._grid.display = False
        self._crows.display = self._view == "crows"
        with contextlib.suppress(Exception):
            self.query_one("#planning_sidebar").display = planning and self._sidebar_visible
        self._plans.display = planning and self._sidebar_visible
        self._notes_list.display = planning and self._sidebar_visible
        self._plan_doc.display = (
            planning and self._active_document == "plan" and self._has_selected_plan
        )
        self._notes_doc.display = planning and self._active_document == "note"
        self._collab_chat.display = collab_chat_on
        self._dispatch.display = self._view == "schedule"
        self._chat.display = self._view != "schedule"
        # The shared PaneMirror is now only used by planning's collab-raw
        # toggle; CrowsView owns its own mirror for the enlarged tile.
        self._mirror.display = collab_raw_on
        self._mirror.styles.width = "1fr"
        if collab_chat_on:
            self.run_worker(self._refresh_collab_chat(), exclusive=True, group="collab_chat")

    def action_toggle_sidebar(self) -> None:
        self._sidebar_visible = not self._sidebar_visible
        self._apply_mode()
        self.notify(f"docs sidebar: {'on' if self._sidebar_visible else 'off'}", timeout=2)

    def action_toggle_collab_raw(self) -> None:
        # Only meaningful in collaborator planning mode; harmless elsewhere.
        self._collab_raw = not self._collab_raw
        if self._collab_raw:
            self._sync_collaborator_mirror_session()
        self._apply_mode()
        self.notify(
            f"collaborator view: {'raw tmux pane' if self._collab_raw else 'parsed chat'}",
            timeout=2,
        )

    def on_chat_input_user_message(self, event: ChatInput.UserMessage) -> None:
        # Own worker group: a chat dispatch may spend a minute inside
        # ensure_collaborator(); it must not be cancelled by an unrelated
        # exclusive=True UI worker (plan/notes render, pane mirror, …) in the
        # default group — that cancellation raises CancelledError, which is a
        # BaseException and slips past the handlers below, killing the spawn
        # silently.
        self.run_worker(self._dispatch_chat(event.text), exclusive=False, group="chat")

    async def _dispatch_chat(self, text: str) -> None:
        # Classify in most-specific-first order; keep ChatInput dumb.
        if text.startswith("!"):
            await self._run_shell_cmd(text[1:].strip())
            return
        if _is_vim_style_quit(text):
            self.exit()
            return
        if text.startswith("/"):
            await self._handle_slash(text)
            return
        # Serialize ensure+send so a flurry of messages during cold-start
        # doesn't race two collaborator spawns or interleave send_keys.
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
            # Optimistic echo + spinner; the next pane parse reconciles it.
            self._collab_chat.add_turn("you", text)
            self._collab_chat.add_status("collaborator is thinking…")
        self.notify("→ collaborator", timeout=2)

    async def _run_shell_cmd(self, cmd: str) -> None:
        if not cmd:
            return
        if self._shell_session:
            try:
                await tmux.kill_session(self._shell_session)
            except tmux.TmuxError:
                pass
            self._shell_session = None
        session_name = f"murder-shell-{int(time.monotonic() * 1000) % 1_000_000}"
        try:
            await tmux.create_session(session_name, self.runtime.repo_root)
            await tmux.send_keys(session_name, cmd)
            self._shell_session = session_name
            self._mirror.set_session(session_name)
            self.run_worker(self._mirror.refresh_pane(), exclusive=True, group="mirror")
            self.notify(f"! {cmd}", timeout=2)
        except Exception as e:
            self.notify(f"shell error: {e}", severity="error", timeout=5)

    async def _handle_slash(self, text: str) -> None:
        parts = text[1:].split()
        if not parts:
            return
        cmd, *args = parts
        if cmd == "murder":
            await self._kick_ready()
        elif cmd == "exit":
            self.exit()
        elif cmd == "note":
            body = " ".join(args).strip()
            if body:
                await self._slash_note_submit(body)
            else:
                self.action_open_note_capture()
        else:
            self.notify(f"unknown command: /{cmd}", severity="warning", timeout=3)

    def action_open_note_capture(self) -> None:
        screen = NoteCaptureScreen(
            initial_draft=self._note_capture_draft,
            load_recent_rows=self._sync_recent_note_entries,
        )
        self.push_screen(screen, self._on_note_capture_closed)

    def _sync_recent_note_entries(self) -> list[dict]:
        return self.runtime.read_model.get_notetaker_recent_entries(RECENT_NOTE_ROWS)

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
        """Submit note capture through the service command and update UI on completion."""
        self.notify("capturing note...", timeout=3)
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
        if not note_name:
            self.notify("note saved (unknown name)", timeout=4)
            return
        self._refresh_db_views()
        self._active_document = "note"
        self._plan_doc.display = False
        self._notes_doc.display = True
        self._notes_list.select_name(note_name)
        self.run_worker(
            self._render_note(note_name),
            exclusive=True,
            group="notedoc",
            exit_on_error=False,
        )
        self.notify(f"note ready: {note_name}.md", timeout=4)

    def action_focus_chat(self) -> None:
        if self.focused is not self._chat:
            self._pre_chat_focus = self.focused
        self.set_focus(self._chat)

    def action_restore_focus(self) -> None:
        target = self._pre_chat_focus
        self._pre_chat_focus = None
        if target is not None and target.display:
            self.set_focus(target)
            return
        # Fallback: focus current view's primary widget
        if self._view == "planning":
            self.set_focus(self._notes_list if self._active_document == "note" else self._plans)
        elif self._view == "crows":
            if not self._crows.focus_first_tile():
                self.set_focus(self._crows)
        else:
            self.set_focus(None)

    def action_show_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_show_help_force(self) -> None:
        self.push_screen(HelpScreen())

    # ── pane focus traversal (VISION §4.3) ─────────────────────────────────
    # ctrl+hjkl / ctrl+arrows move focus between sibling panes in reading
    # order. Bare hjkl/arrows are *not* bound here, so the focused widget
    # (chat input, RichLog, plan doc, ...) keeps its intra-pane motion.

    def _ordered_focusable_panes(self) -> list[Widget]:
        """Top-level panes currently visible in the active place."""
        candidates: list[Widget] = []
        if self._view == "planning":
            doc = self._notes_doc if self._active_document == "note" else self._plan_doc
            if self._collab_raw:
                candidates = [self._plans, self._notes_list, doc, self._mirror, self._chat]
            else:
                candidates = [self._plans, self._notes_list, doc, self._collab_chat, self._chat]
        elif self._view == "crows":
            candidates = [self._crows, self._chat]
        else:  # schedule/dispatch
            try:
                candidates = [self._dispatch.query_one(ScheduleTicketsTable)]
            except Exception:
                candidates = [self._dispatch]
        return [w for w in candidates if w.display]

    def _shift_focus(self, delta: int) -> None:
        panes = self._ordered_focusable_panes()
        if not panes:
            return
        current = self.focused
        idx = -1
        for i, pane in enumerate(panes):
            if current is pane or (current is not None and current in pane.walk_children()):
                idx = i
                break
        if idx >= 0:
            target_idx = (idx + delta) % len(panes)
        else:
            target_idx = 0 if delta > 0 else len(panes) - 1
        target = panes[target_idx]
        # Crows tail-wall is itself a container; focus the first tile.
        if target is self._crows and self._crows.focus_first_tile():
            return
        self.set_focus(target)

    def action_focus_next_region(self) -> None:
        self._shift_focus(1)

    def action_focus_previous_region(self) -> None:
        self._shift_focus(-1)

    def action_focus_right(self) -> None:
        self._shift_focus(1)

    def action_focus_left(self) -> None:
        self._shift_focus(-1)

    def action_focus_down(self) -> None:
        self._shift_focus(1)

    def action_focus_up(self) -> None:
        self._shift_focus(-1)

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
        esc = event.escalation
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
        self._dispatch_ctrl.open_carve_screen(ticket_id)

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
        snapshot = self._crow_snapshot or self.runtime.read_model.get_crow_snapshot()
        for session in snapshot.sessions:
            if session.role == "collaborator" and session.status in ("running", "idle"):
                if session.session_name:
                    self._mirror.set_session(session.session_name)
                    self.run_worker(self._mirror.refresh_pane(), exclusive=True, group="mirror")
                return

    def _crow_session_for_ticket(self, ticket_id: str) -> str | None:
        snapshot = self._crow_snapshot or self.runtime.read_model.get_crow_snapshot()
        for session in snapshot.sessions:
            if (
                session.ticket_id == ticket_id
                and session.role == "crow"
                and session.status in ("running", "idle")
            ):
                return session.session_name
        return None
