"""Top-level Textual app — wires header, ticket grid, pane mirror, and
escalation strip onto the running Runtime."""

from __future__ import annotations

import asyncio
import json
import time
from typing import TYPE_CHECKING
from uuid import uuid4

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Footer, Static

from murder import db as dbmod
from murder import notes as notes_mod
from murder import tmux
from murder.bus.protocol import CommandEvent
from murder.config import Config
from murder.storage.paths import note_md
from murder.tui.agent_grid import AgentGrid
from murder.tui.chat_input import ChatInput
from murder.tui.escalation_strip import EscalationStrip
from murder.tui.header import Header
from murder.tui.pane_mirror import PaneMirror
from murder.tui.plan_view import (
    ChatLog,
    NotesDocument,
    NotesList,
    PlanDocument,
    PlanList,
)
from murder.tui.schedule_view import ScheduleView
from murder.tui.settings_screen import SettingsScreen
from murder.tui.themes import CUSTOM_THEMES
from murder.tui.ticket_grid import TicketGrid
from murder.user_config import UserConfig, load_user_config

if TYPE_CHECKING:
    from murder.orchestrator import Orchestrator
    from murder.runtime import Runtime

COLLABORATOR_START_TIMEOUT_S = 120.0
COMMAND_POLL_S = 0.05
SCHEDULE_USAGE_DEBOUNCE_S = 20.0
CTRL_C_DOUBLE_TAP_S = 1.5


def _chat_target_label(view: str, planning_mode: str) -> str:
    if view == "planning" and planning_mode == "notetaker":
        return "notetaker"
    return "collaborator"


class HelpScreen(ModalScreen[None]):
    """Small in-app glossary and key reference."""

    BINDINGS = [("escape", "dismiss", "Close"), ("f1", "dismiss", "Close")]
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
                        "/exit    quit murder",
                        "!<cmd>   run shell command (output in pane mirror)",
                        ":wq :q!  vim-style quit",
                        "",
                        "[b]keys[/b]",
                        "F6 kick ready · F2 focus chat · F1 help",
                        "ctrl+1/2/3  switch views  ·  [ and ] cycle views",
                        "ctrl+t  toggle planning mode (notetaker ⇄ collaborator)",
                        "ctrl+b  toggle docs sidebar (notes / plans filetree)",
                        "ctrl+y  collaborator: parsed chat ⇄ raw tmux pane",
                        "ctrl+c twice  force quit  ·  escape  unfocus chat",
                        "j/k or ↑/↓  vim-style navigation in lists and logs",
                        "r refresh · u refresh usage",
                        "ctrl+p settings (harnesses, models, theme)",
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
        ("ctrl+p", "open_settings", "Settings"),
        ("ctrl+1", "view_planning", "Planning"),
        ("ctrl+2", "view_crows", "Crows"),
        ("ctrl+3", "view_schedule", "Schedule"),
        ("[", "previous_view", "Prev view"),
        ("]", "next_view", "Next view"),
        ("ctrl+t", "toggle_planning_mode", "Plan mode"),
        ("ctrl+b", "toggle_sidebar", "Docs sidebar"),
        ("ctrl+y", "toggle_collab_raw", "Raw pane"),
        ("q", "quit", "Quit"),
        ("r", "refresh_now", "Refresh"),
        ("u", "collect_usage", "Usage"),
        ("f6", "kick_ready", "Kick"),
        ("f2", "focus_chat", "Chat"),
        ("f1", "show_help_force", "Help"),
        ("?", "show_help", "Help"),
    ]

    CSS = """
    Screen {
        layout: vertical;
    }
    #body {
        height: 1fr;
    }
    TicketGrid {
        width: 50%;
        border: solid $border;
    }
    AgentGrid {
        width: 56%;
        border: solid $border;
    }
    PlanList {
        width: 18%;
        border: solid $border;
    }
    NotesList {
        width: 18%;
        border: solid $border;
    }
    """

    VIEWS = ("planning", "crows", "schedule")

    def __init__(self, runtime: Runtime, orchestrator: Orchestrator | None = None) -> None:
        super().__init__()
        self.runtime = runtime
        self.orchestrator = orchestrator
        self._header = Header(runtime.config.project.name)
        self._grid = TicketGrid()
        self._agents = AgentGrid()
        self._plans = PlanList()
        self._plan_doc = PlanDocument()
        self._notes_list = NotesList()
        self._notes_doc = NotesDocument()
        self._notetaker_chat = ChatLog(agent_label="notetaker")
        self._collab_chat = ChatLog(agent_label="collaborator")
        self._schedule = ScheduleView()
        self._mirror = PaneMirror()
        self._escalations = EscalationStrip()
        self._chat = ChatInput()
        self._collab_lock = asyncio.Lock()
        self._collab_chat_lock = asyncio.Lock()
        self._notetaker_lock = asyncio.Lock()
        self._planning_mode = "notetaker"  # "notetaker" | "collaborator"
        self._sidebar_visible = True
        self._collab_raw = False  # ctrl+y: show the raw tmux pane instead of the parsed chat
        self._notetaker_loaded = False
        self._user_config: UserConfig = load_user_config()
        self._view = "planning"
        self._last_schedule_usage_attempt_at: float | None = None
        self._pre_chat_focus = None
        self._has_selected_plan = False
        self._shell_session: str | None = None
        self._last_ctrl_c: float = 0.0
        for theme in CUSTOM_THEMES:
            self.register_theme(theme)

    def compose(self) -> ComposeResult:
        yield self._header
        with Horizontal(id="body"):
            yield self._grid
            yield self._agents
            yield self._plans
            yield self._plan_doc
            yield self._notes_list
            yield self._notes_doc
            yield self._notetaker_chat
            yield self._collab_chat
            yield self._schedule
            yield self._mirror
        yield self._escalations
        yield self._chat
        yield Footer()

    def on_mount(self) -> None:
        if self._user_config.tui.theme in self.available_themes:
            self.theme = self._user_config.tui.theme
        self.sub_title = str(self.runtime.repo_root)
        self._apply_mode()
        self._refresh_db_views()
        self.set_focus(self._chat)
        if self.runtime.config.project.name == "TODO_SET_ME":
            self.notify(
                "Project name is unset — open Settings (ctrl+p) to update roles.yaml.",
                severity="warning",
                timeout=10,
            )
        interval_s = max(self.runtime.config.tui.refresh_ms, 250) / 1000
        self.set_interval(interval_s, self._refresh_db_views)
        self.set_interval(max(interval_s, 1.0), self._refresh_pane)
        if self._view == "planning" and self._planning_mode == "notetaker":
            self.run_worker(self._enter_notetaker(), exclusive=True, group="notetaker")

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
        result = await self._submit_command(
            target_worker="usage-probe",
            kind="state.harness_usage.sample",
            payload={"trigger": "manual_u_key"},
            timeout_s=20.0,
        )
        if result is None:
            return
        stored = int(result.get("stored", 0))
        failures = int(result.get("failures", 0))
        self._refresh_db_views()
        if stored or failures:
            self.notify(
                f"Sampled {stored} harness usages ({failures} failed).",
                timeout=4,
            )

    def _refresh_db_views(self) -> None:
        db = self.runtime.db
        self._header.refresh_counts(db)
        self._grid.refresh_from_db(db)
        self._agents.refresh_from_db(db)
        self._plans.refresh_from_db(db)
        self._notes_list.refresh_from_db(db)
        self._schedule.refresh_from_db(db)
        self._escalations.refresh_from_db(db)
        if self._view == "planning":
            if self._planning_mode == "collaborator" and self._plans.selected_name:
                self.run_worker(
                    self._render_plan(self._plans.selected_name),
                    exclusive=True, group="plandoc",
                )
            elif self._planning_mode == "notetaker":
                self.run_worker(self._render_notes(), exclusive=True, group="notes")

    async def _refresh_pane(self) -> None:
        await self._mirror.refresh_pane()
        if (
            self._view == "planning"
            and self._planning_mode == "collaborator"
            and not self._collab_raw
        ):
            await self._refresh_collab_chat()

    def on_ticket_grid_ticket_selected(self, event: TicketGrid.TicketSelected) -> None:
        self._mirror.set_session(self._crow_session_for_ticket(event.ticket_id))
        self.run_worker(self._mirror.refresh_pane(), exclusive=True, group="mirror")

    def on_agent_grid_agent_highlighted(self, event: AgentGrid.AgentHighlighted) -> None:
        self._mirror.set_session(event.session)
        self.run_worker(self._mirror.refresh_pane(), exclusive=True, group="mirror")

    def on_agent_grid_agent_opened(self, event: AgentGrid.AgentOpened) -> None:
        self._mirror.set_session(event.session)
        self.run_worker(self._mirror.refresh_pane(), exclusive=True, group="mirror")
        hint = f"tmux attach -t {event.session}" if event.session else "(no session)"
        self.notify(f"attach: {hint}", timeout=6)

    def on_plan_list_plan_highlighted(self, event: PlanList.PlanHighlighted) -> None:
        if self._view == "planning":
            if not self._has_selected_plan:
                self._has_selected_plan = True
                self._plan_doc.display = True
            self.run_worker(self._render_plan(event.name), exclusive=True, group="plandoc")

    async def on_plan_list_plan_opened(self, event: PlanList.PlanOpened) -> None:
        await self._open_plan(event.name)

    def on_notes_list_note_highlighted(self, event: NotesList.NoteHighlighted) -> None:
        del event  # _active_note_name() reads the list cursor directly
        if self._view == "planning" and self._planning_mode == "notetaker":
            self.run_worker(self._render_notes(), exclusive=True, group="notes")

    async def on_notes_list_note_opened(self, event: NotesList.NoteOpened) -> None:
        if self._view == "planning" and self._planning_mode == "notetaker":
            await self._open_note(event.name)

    async def _render_plan(self, name: str) -> None:
        await self.runtime.reconcile_plan(name)
        db = self.runtime.db
        if db is None:
            return
        row = db.execute(
            """
            SELECT materialized_path, sync_state, parse_error, conflict_reason
              FROM plans
             WHERE name = ?
            """,
            (name,),
        ).fetchone()
        if row is None:
            return
        path = self.runtime.repo_root / row["materialized_path"]
        if path.exists():
            text = path.read_text(encoding="utf-8")
        else:
            text = f"# {name}\n\nMissing materialized file: `{row['materialized_path']}`\n"
        if row["sync_state"] == "parse_error":
            text = f"# {name}\n\nParse error: {row['parse_error']}\n\n```markdown\n{text}\n```"
        elif row["sync_state"] == "conflict":
            text = f"# {name}\n\nConflict: {row['conflict_reason']}\n\n{text}"
        self._plan_doc.border_title = name
        await self._plan_doc.update(text)

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

    async def _open_note(self, name: str) -> None:
        path = self.runtime.note_path_for(name)
        with self.suspend():
            code = self.runtime.open_editor_blocking(path, self._user_config.tui.editor)
        if code != 0:
            self.notify(f"editor exited with {code}", severity="warning", timeout=5)
        if self.runtime.note_sync is not None:
            await self.runtime.note_sync.reconcile_file(path)
        self._refresh_db_views()
        await self._render_notes()
        self.set_focus(self._notes_doc)

    # ── notetaker mode ─────────────────────────────────────────────────────

    def _active_note_name(self) -> str | None:
        # Browsing an older note in the sidebar wins; otherwise show the live
        # (today's) note the notetaker is appending to.
        if self._notes_list.display and self._notes_list.selected_name:
            return self._notes_list.selected_name
        if self.runtime.db is not None:
            return dbmod.latest_note_name(self.runtime.db) or notes_mod.today_name()
        return notes_mod.today_name()

    async def _render_notes(self) -> None:
        db = self.runtime.db
        if db is None:
            return
        name = self._active_note_name()
        if name is None:
            return
        row = dbmod.get_note(db, name)
        body = str(row["body"]) if row else ""
        if not body.strip():
            path = note_md(self.runtime.repo_root, name)
            if path.exists():
                body = path.read_text(encoding="utf-8")
        # Avoid stale-placeholder lock-in on startup: render from source of
        # truth every refresh tick rather than short-circuiting by timestamp.
        await self._notes_doc.show(name, body)

    async def _enter_notetaker(self) -> None:
        await self._render_notes()
        if not self._notetaker_loaded:
            self._notetaker_chat.clear()
            self._notetaker_loaded = True
        await self._render_notes()

    async def _dispatch_notetaker(self, text: str) -> None:
        async with self._notetaker_lock:
            if not self._notetaker_loaded:
                self._notetaker_chat.clear()
                self._notetaker_loaded = True
            self._notetaker_chat.add_turn("you", text)
            self._notetaker_chat.add_status("notetaker is thinking…")
            result = await self._submit_command(
                target_worker="orchestrator",
                kind="notetaker.chat.send",
                payload={"text": text},
                timeout_s=COLLABORATOR_START_TIMEOUT_S,
            )
            if result is None:
                return
            reply = result.get("reply")
            self._notetaker_chat.add_turn("notetaker", str(reply or ""))
        await self._render_notes()

    # ── collaborator mode ──────────────────────────────────────────────────

    async def _refresh_collab_chat(self) -> None:
        """Re-parse the collaborator's tmux pane into the chat transcript.

        Lock-guarded so overlapping refresh-pane ticks don't race two parses
        onto the same conversation log.
        """
        if self._collab_chat_lock.locked():
            return
        async with self._collab_chat_lock:
            if not (self._view == "planning" and self._planning_mode == "collaborator"):
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
                self._collab_chat.add_status("(no collaborator yet — type a message to start one)")
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
        self.run_worker(self._on_schedule_view_enter(), exclusive=True, group="usage")

    async def _on_schedule_view_enter(self) -> None:
        now = time.monotonic()
        if (
            self._last_schedule_usage_attempt_at is not None
            and now - self._last_schedule_usage_attempt_at < SCHEDULE_USAGE_DEBOUNCE_S
        ):
            return
        self._last_schedule_usage_attempt_at = now
        result = await self._submit_command(
            target_worker="usage-probe",
            kind="scheduler.probe_usage",
            payload={"trigger": "schedule_view_enter"},
            timeout_s=20.0,
        )
        if result is None:
            return
        stored = int(result.get("stored", 0))
        failures = int(result.get("failures", 0))
        self._refresh_db_views()
        if stored or failures:
            self.notify(
                f"Schedule usage: {stored} ok, {failures} failed",
                timeout=4,
            )

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
        self._refresh_db_views()  # also re-renders the planning doc for the active mode
        if self._view == "planning" and self._planning_mode == "notetaker":
            self.run_worker(self._enter_notetaker(), exclusive=True, group="notetaker")

    def _apply_mode(self) -> None:
        planning = self._view == "planning"
        note_mode = planning and self._planning_mode == "notetaker"
        collab_mode = planning and self._planning_mode == "collaborator"
        collab_chat_on = collab_mode and not self._collab_raw
        collab_raw_on = collab_mode and self._collab_raw
        self._chat.set_recipient(_chat_target_label(self._view, self._planning_mode))
        self._header.set_view(self._view, self._planning_mode if planning else None)
        self._grid.display = False
        self._agents.display = self._view == "crows"
        self._plans.display = collab_mode and self._sidebar_visible
        self._plan_doc.display = collab_mode and self._has_selected_plan
        self._collab_chat.display = collab_chat_on
        self._notes_list.display = note_mode and self._sidebar_visible
        self._notes_doc.display = note_mode
        self._notetaker_chat.display = note_mode
        self._schedule.display = self._view == "schedule"
        self._chat.display = self._view != "schedule"
        self._mirror.display = collab_raw_on or self._view == "crows"
        self._mirror.styles.width = "1fr"
        if collab_chat_on:
            self.run_worker(self._refresh_collab_chat(), exclusive=True, group="collab_chat")

    def action_toggle_planning_mode(self) -> None:
        if self._view != "planning":
            self._set_view("planning")
        self._planning_mode = (
            "collaborator" if self._planning_mode == "notetaker" else "notetaker"
        )
        self._apply_mode()
        self.notify(f"planning mode: {self._planning_mode}", timeout=2)
        if self._planning_mode == "notetaker":
            self.run_worker(self._enter_notetaker(), exclusive=True, group="notetaker")

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
        if text in {":wq", ":q!"}:
            self.exit()
            return
        if text.startswith("/"):
            await self._handle_slash(text)
            return
        if self._view == "planning" and self._planning_mode == "notetaker":
            await self._dispatch_notetaker(text)
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
        else:
            self.notify(f"unknown command: /{cmd}", severity="warning", timeout=3)

    def action_quit(self) -> None:
        if self._insert_if_chat_focused("q"):
            return
        self.exit()

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
            self.set_focus(
                self._notes_list if self._planning_mode == "notetaker" else self._plans
            )
        elif self._view == "crows":
            self.set_focus(self._agents)
        else:
            self.set_focus(None)

    def action_show_help(self) -> None:
        if self._insert_if_chat_focused("?"):
            return
        self.push_screen(HelpScreen())

    def action_show_help_force(self) -> None:
        self.push_screen(HelpScreen())

    def action_kick_ready(self) -> None:
        self.run_worker(self._kick_ready(), exclusive=False)

    async def _kick_ready(self) -> None:
        result = await self._submit_command(
            target_worker="orchestrator",
            kind="scheduler.kickoff_ready",
            payload={},
            timeout_s=30.0,
        )
        if result is None:
            return
        kicked = list(result.get("kicked", []))
        self.notify(
            f"kicked: {', '.join(kicked)}" if kicked else "no ready tickets",
            timeout=3,
        )
        self._refresh_db_views()

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
        if self.runtime.bus is None or self.runtime.db is None or self.runtime.run_id is None:
            if notify_errors:
                self.notify("service bus unavailable", severity="error", timeout=4)
            return None
        command = CommandEvent(
            run_id=self.runtime.run_id,
            agent_id="tui",
            target_worker=target_worker,
            kind=kind,
            payload=payload,
            correlation_id=f"tui-{uuid4()}",
            idempotency_key=f"tui-{kind}-{uuid4()}",
        )
        await self.runtime.bus.publish(command)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            row = self.runtime.db.execute(
                "SELECT status, result_json, last_error FROM commands WHERE id = ?",
                (str(command.id),),
            ).fetchone()
            if row is None:
                await asyncio.sleep(COMMAND_POLL_S)
                continue
            status = str(row["status"])
            if status == "done":
                raw = row["result_json"]
                return json.loads(raw) if raw else {}
            if status == "failed":
                message = str(row["last_error"] or f"{kind} failed")
                if notify_errors:
                    self.notify(message, severity="error", timeout=8)
                return None
            await asyncio.sleep(COMMAND_POLL_S)
        if notify_errors:
            self.notify(f"{kind} timed out", severity="error", timeout=8)
        return None

    def _sync_collaborator_mirror_session(self) -> None:
        if self.runtime.db is None:
            return
        row = self.runtime.db.execute(
            "SELECT session FROM agents WHERE role='collaborator' "
            "AND status IN ('running','idle') LIMIT 1"
        ).fetchone()
        if row is None:
            return
        self._mirror.set_session(str(row["session"]))
        self.run_worker(self._mirror.refresh_pane(), exclusive=True, group="mirror")

    def _crow_session_for_ticket(self, ticket_id: str) -> str | None:
        if self.runtime.db is None:
            return None
        row = self.runtime.db.execute(
            "SELECT session FROM agents WHERE ticket_id = ? AND role = 'crow' "
            "AND status IN ('running','idle') LIMIT 1",
            (ticket_id,),
        ).fetchone()
        if row is None:
            return None
        return str(row["session"])
