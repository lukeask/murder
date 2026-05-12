"""Top-level Textual app — wires header, ticket grid, pane mirror, and
escalation strip onto the running Runtime."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Footer, Static

from murder import db as dbmod
from murder import notes as notes_mod
from murder import tmux
from murder.agents.notetaker import NotetakerAgent
from murder.harnesses.usage_sampling import sample_harness_usages_for_config
from murder.tui.agent_grid import AgentGrid
from murder.tui.chat_input import ChatInput
from murder.tui.escalation_strip import EscalationStrip
from murder.tui.header import Header
from murder.tui.pane_mirror import PaneMirror
from murder.tui.plan_view import (
    NotesDocument,
    NotesList,
    NotetakerChat,
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
SCHEDULE_USAGE_DEBOUNCE_S = 20.0
CTRL_C_DOUBLE_TAP_S = 1.5


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
                        "ctrl+c twice  force quit  ·  escape  unfocus chat",
                        "j/k  vim navigation in lists",
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
        self._notetaker_chat = NotetakerChat()
        self._schedule = ScheduleView()
        self._mirror = PaneMirror()
        self._escalations = EscalationStrip()
        self._chat = ChatInput()
        self._collab_lock = asyncio.Lock()
        self._notetaker_lock = asyncio.Lock()
        self._planning_mode = "notetaker"  # "notetaker" | "collaborator"
        self._sidebar_visible = True
        self._notetaker_loaded = False
        self._last_notes_sig: tuple[str, str] | None = None
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
        if self.runtime.db is None:
            return
        stored, failures = await sample_harness_usages_for_config(self.runtime)
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

    def on_ticket_grid_ticket_selected(self, event: TicketGrid.TicketSelected) -> None:
        crow = self.runtime.get_crow(event.ticket_id)
        session = crow.session if crow is not None else None
        self._mirror.set_session(session)
        self.run_worker(self._mirror.refresh_pane(), exclusive=True, group="mirror")

    def on_agent_grid_agent_highlighted(self, event: AgentGrid.AgentHighlighted) -> None:
        self._mirror.set_session(event.session)
        self.run_worker(self._mirror.refresh_pane(), exclusive=True, group="mirror")

    def on_agent_grid_agent_opened(self, event: AgentGrid.AgentOpened) -> None:
        self._mirror.set_session(event.session)
        self.run_worker(self._mirror.refresh_pane(), exclusive=True, group="mirror")
        agent = self.runtime.get_agent(event.agent_id)
        hint = agent.attach_hint() if agent is not None else (
            f"tmux attach -t {event.session}" if event.session else "(no session)"
        )
        self.notify(f"attach: {hint}", timeout=6)

    def on_plan_list_plan_highlighted(self, event: PlanList.PlanHighlighted) -> None:
        if self._view == "planning":
            if not self._has_selected_plan:
                self._has_selected_plan = True
                self._plan_doc.display = True
            self.run_worker(self._render_plan(event.name), exclusive=True, group="plandoc")

    def on_plan_list_plan_opened(self, event: PlanList.PlanOpened) -> None:
        self.run_worker(self._open_plan(event.name), exclusive=True, group="editor")

    def on_notes_list_note_highlighted(self, event: NotesList.NoteHighlighted) -> None:
        del event  # _active_note_name() reads the list cursor directly
        if self._view == "planning" and self._planning_mode == "notetaker":
            self.run_worker(self._render_notes(), exclusive=True, group="notes")

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
        code = await self.runtime.open_plan_in_editor(name, self._user_config.tui.editor)
        if code != 0:
            self.notify(f"editor exited with {code}", severity="warning", timeout=5)
        self._refresh_db_views()
        await self._render_plan(name)

    # ── notetaker mode ─────────────────────────────────────────────────────

    def _notetaker_agent(self) -> NotetakerAgent | None:
        agent = self.runtime.get_agent("notetaker-0")
        return agent if isinstance(agent, NotetakerAgent) else None

    def _active_note_name(self) -> str | None:
        # Browsing an older note in the sidebar wins; otherwise show the live
        # (today's) note the notetaker is appending to.
        if self._notes_list.display and self._notes_list.selected_name:
            return self._notes_list.selected_name
        agent = self._notetaker_agent()
        if agent is not None:
            return agent.note_name
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
        sig = (name, str(row["updated_at"]) if row else "")
        if sig == self._last_notes_sig:
            return
        self._last_notes_sig = sig
        await self._notes_doc.show(name, body)

    async def _enter_notetaker(self) -> None:
        await self._render_notes()
        if self.orchestrator is None:
            return
        async with self._notetaker_lock:
            try:
                agent_id = await self.orchestrator.ensure_notetaker()
            except Exception as e:  # noqa: BLE001
                self.notify(f"notetaker unavailable: {e}", severity="error", timeout=6)
                return
            agent = self.runtime.get_agent(agent_id)
            if not isinstance(agent, NotetakerAgent):
                return
            if not self._notetaker_loaded:
                self._notetaker_chat.clear()
                for who, text in agent.transcript_for_ui():
                    self._notetaker_chat.add_turn(who, text)
                self._notetaker_loaded = True
        await self._render_notes()

    async def _dispatch_notetaker(self, text: str) -> None:
        if self.orchestrator is None:
            self.notify("notetaker: no orchestrator attached", severity="error", timeout=3)
            return
        async with self._notetaker_lock:
            try:
                agent_id = await self.orchestrator.ensure_notetaker()
            except Exception as e:  # noqa: BLE001
                self.notify(f"notetaker spawn failed: {e}", severity="error", timeout=8)
                return
            agent = self.runtime.get_agent(agent_id)
            if not isinstance(agent, NotetakerAgent):
                self.notify("notetaker vanished after spawn", severity="error", timeout=5)
                return
            if not self._notetaker_loaded:
                self._notetaker_chat.clear()
                for who, t in agent.transcript_for_ui():
                    self._notetaker_chat.add_turn(who, t)
                self._notetaker_loaded = True
            self._notetaker_chat.add_turn("you", text)
            self._notetaker_chat.add_status("notetaker is thinking…")
            try:
                reply = await agent.reply_to(text)
            except Exception as e:  # noqa: BLE001
                self.notify(f"notetaker error: {e}", severity="error", timeout=6)
                return
            self._notetaker_chat.add_turn("notetaker", reply)
        await self._render_notes()

    def action_view_planning(self) -> None:
        self._set_view("planning")

    def action_view_crows(self) -> None:
        self._set_view("crows")

    def action_view_schedule(self) -> None:
        self._set_view("schedule")
        self.run_worker(self._on_schedule_view_enter(), exclusive=True, group="usage")

    async def _on_schedule_view_enter(self) -> None:
        if self.runtime.db is None:
            return
        now = time.monotonic()
        if (
            self._last_schedule_usage_attempt_at is not None
            and now - self._last_schedule_usage_attempt_at < SCHEDULE_USAGE_DEBOUNCE_S
        ):
            return
        self._last_schedule_usage_attempt_at = now
        stored, failures = await sample_harness_usages_for_config(self.runtime)
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
        self._header.set_view(self._view, self._planning_mode if planning else None)
        self._grid.display = False
        self._agents.display = self._view == "crows"
        self._plans.display = collab_mode and self._sidebar_visible
        self._plan_doc.display = collab_mode and self._has_selected_plan
        self._notes_list.display = note_mode and self._sidebar_visible
        self._notes_doc.display = note_mode
        self._notetaker_chat.display = note_mode
        self._schedule.display = self._view == "schedule"
        self._mirror.display = collab_mode or self._view == "crows"
        self._mirror.styles.width = "28%" if collab_mode else "1fr"

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

    def on_chat_input_user_message(self, event: ChatInput.UserMessage) -> None:
        self.notify(f"you: {event.text[:90]}", timeout=2)
        if self.orchestrator is None:
            self.notify("chat: no orchestrator attached", severity="error", timeout=3)
            return
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
            already = self.runtime.db and self.runtime.db.execute(
                "SELECT agent_id FROM agents WHERE role='collaborator' "
                "AND status IN ('running','idle') LIMIT 1"
            ).fetchone()
            if not already:
                self.notify("starting collaborator… (first launch can take a minute)", timeout=60)
            try:
                agent_id = await asyncio.wait_for(
                    self.orchestrator.ensure_collaborator(),
                    timeout=COLLABORATOR_START_TIMEOUT_S,
                )
            except asyncio.TimeoutError:
                self._record_ui_escalation(
                    "Collaborator startup timed out; check Claude Code install/auth "
                    "or run `murder doctor`."
                )
                self.notify(
                    "collaborator startup timed out; check install/auth or `murder doctor`",
                    severity="error",
                    timeout=10,
                )
                return
            except asyncio.CancelledError:
                # Some other worker (or shutdown) cancelled us mid-spawn. Don't
                # swallow it silently — say so, then let Textual handle it.
                self.notify("collaborator startup cancelled", severity="warning", timeout=6)
                raise
            except Exception as e:
                self._record_ui_escalation(f"Collaborator startup failed: {e}")
                self.notify(f"collaborator spawn failed: {e}", severity="error", timeout=10)
                return
            agent = self.runtime.get_agent(agent_id)
            if agent is None:
                self.notify("collaborator vanished after spawn", severity="error", timeout=5)
                return
            self._mirror.set_session(agent.session)
            self.run_worker(self._mirror.refresh_pane(), exclusive=True, group="mirror")
            try:
                await agent.send(text)
            except Exception as e:
                self.notify(f"send failed: {e}", severity="error", timeout=5)
                return
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
        if self.orchestrator is None:
            self.notify("kickoff unavailable: no orchestrator", severity="error", timeout=4)
            return
        try:
            kicked = await self.orchestrator.kickoff_ready()
        except Exception as e:
            self.notify(f"kickoff failed: {e}", severity="error", timeout=5)
            return
        self.notify(
            f"kicked: {', '.join(kicked)}" if kicked else "no ready tickets",
            timeout=3,
        )
        self._refresh_db_views()

    def _record_ui_escalation(self, reason: str) -> None:
        if self.runtime.db is None:
            return
        dbmod.insert_escalation(
            self.runtime.db,
            ticket_id=None,
            severity=2,
            reason=reason,
            to_recipient="user",
        )
        self._escalations.refresh_from_db(self.runtime.db)

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
        self._user_config = load_user_config()
        if self._user_config.tui.theme and self._user_config.tui.theme in self.available_themes:
            self.theme = self._user_config.tui.theme
        self.notify("Settings saved.", timeout=3)
