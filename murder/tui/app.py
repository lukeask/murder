"""Top-level Textual app — wires header, ticket grid, pane mirror, and
escalation strip onto the running Runtime."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, is_dataclass
from typing import TYPE_CHECKING

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Footer, Static

from murder import db as dbmod
from murder.tui.agent_grid import AgentGrid
from murder.tui.chat_input import ChatInput
from murder.tui.escalation_strip import EscalationStrip
from murder.tui.header import Header
from murder.tui.pane_mirror import PaneMirror
from murder.tui.plan_view import PlanDocument, PlanList
from murder.tui.schedule_view import ScheduleView
from murder.tui.themes import CUSTOM_THEMES
from murder.tui.ticket_grid import TicketGrid
from murder.user_config import UserConfig, load_user_config, save_user_config

if TYPE_CHECKING:
    from murder.orchestrator import Orchestrator
    from murder.runtime import Runtime

COLLABORATOR_START_TIMEOUT_S = 45.0


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
        border: round $primary;
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
                        "monkey: coding agent assigned to one ticket",
                        "crow: a visible agent/session row in the Crows view",
                        "augur: watches a monkey and records progress",
                        "sentinel: handles questions and escalations",
                        "ticket: scoped unit of work with deps, write_set, checklist",
                        "wave: tickets that may run after earlier dependencies finish",
                        "",
                        "[b]slash commands[/b]",
                        "/murder  kick ready tickets",
                        "",
                        "[b]keys[/b]",
                        "F6 kick ready · F2 focus chat · F1 help · 1/2/3 switch views",
                        "[ and ] change view · r refresh · u sample usage · q quit",
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
    BINDINGS = [
        ("ctrl+p", "change_theme", "Theme"),
        ("1", "view_planning", "Planning"),
        ("2", "view_crows", "Crows"),
        ("3", "view_schedule", "Schedule"),
        ("[", "previous_view", "Prev view"),
        ("]", "next_view", "Next view"),
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
        border: round $accent;
    }
    AgentGrid {
        width: 56%;
        border: round $accent;
    }
    PlanList {
        width: 28%;
        border: round $accent;
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
        self._schedule = ScheduleView()
        self._mirror = PaneMirror()
        self._escalations = EscalationStrip()
        self._chat = ChatInput()
        self._collab_lock = asyncio.Lock()
        self._user_config: UserConfig = load_user_config()
        self._persist_theme_changes = False
        self._view = "planning"
        for theme in CUSTOM_THEMES:
            self.register_theme(theme)

    def compose(self) -> ComposeResult:
        yield self._header
        with Horizontal(id="body"):
            yield self._grid
            yield self._agents
            yield self._plans
            yield self._plan_doc
            yield self._schedule
            yield self._mirror
        yield self._escalations
        yield self._chat
        yield Footer()

    def on_mount(self) -> None:
        if self._user_config.tui.theme in self.available_themes:
            self.theme = self._user_config.tui.theme
        self._persist_theme_changes = True
        self.sub_title = str(self.runtime.repo_root)
        self._apply_mode()
        self._refresh_db_views()
        self.set_focus(self._chat)
        if self.runtime.config.project.name == "TODO_SET_ME":
            self.notify(
                "Project name is unset; run `murder config` to replace TODO_SET_ME.",
                severity="warning",
                timeout=10,
            )
        interval_s = max(self.runtime.config.tui.refresh_ms, 250) / 1000
        self.set_interval(interval_s, self._refresh_db_views)
        # Pane mirror cadence is independent — capture-pane is cheap but adds
        # tmux load if the user pegs refresh_ms low.
        self.set_interval(max(interval_s, 1.0), self._refresh_pane)

    def action_refresh_now(self) -> None:
        if self._insert_if_chat_focused("r"):
            return
        self._refresh_db_views()
        self.run_worker(self._mirror.refresh_pane(), exclusive=True)

    def action_collect_usage(self) -> None:
        if self._insert_if_chat_focused("u"):
            return
        self.run_worker(self._collect_usage_snapshots(), exclusive=True)

    async def _collect_usage_snapshots(self) -> None:
        if self.runtime.db is None:
            return
        agents = list(getattr(self.runtime, "_agents", {}).values())
        stored = 0
        unsupported = 0
        for agent in agents:
            harness_session = getattr(agent, "harness_session", None)
            if harness_session is None:
                continue
            result = await harness_session.collect_usage_status()
            if not result.ok or result.data is None:
                unsupported += 1
                continue
            status = result.data
            payload = asdict(status) if is_dataclass(status) else status
            self.runtime.db.execute(
                """
                INSERT INTO harness_usage_snapshots
                    (harness, source, fetched_at, status_json)
                VALUES (?, ?, ?, ?)
                """,
                (
                    status.harness,
                    status.source,
                    status.fetched_at,
                    json.dumps(payload, sort_keys=True, default=str),
                ),
            )
            stored += 1
        self._refresh_db_views()
        if stored or unsupported:
            self.notify(
                f"Sampled {stored} harness usages ({unsupported} unsupported).",
                timeout=4,
            )

    def _refresh_db_views(self) -> None:
        db = self.runtime.db
        self._header.refresh_counts(db)
        self._grid.refresh_from_db(db)
        self._agents.refresh_from_db(db)
        self._plans.refresh_from_db(db)
        self._schedule.refresh_from_db(db)
        self._escalations.refresh_from_db(db)
        if self._view == "planning" and self._plans.selected_name:
            self.run_worker(self._render_plan(self._plans.selected_name), exclusive=True)

    async def _refresh_pane(self) -> None:
        await self._mirror.refresh_pane()

    def on_ticket_grid_ticket_selected(self, event: TicketGrid.TicketSelected) -> None:
        monkey = self.runtime.get_monkey(event.ticket_id)
        session = monkey.session if monkey is not None else None
        self._mirror.set_session(session)
        self.run_worker(self._mirror.refresh_pane(), exclusive=True)

    def on_agent_grid_agent_highlighted(self, event: AgentGrid.AgentHighlighted) -> None:
        self._mirror.set_session(event.session)
        self.run_worker(self._mirror.refresh_pane(), exclusive=True)

    def on_agent_grid_agent_opened(self, event: AgentGrid.AgentOpened) -> None:
        self._mirror.set_session(event.session)
        self.run_worker(self._mirror.refresh_pane(), exclusive=True)
        agent = self.runtime.get_agent(event.agent_id)
        hint = agent.attach_hint() if agent is not None else (
            f"tmux attach -t {event.session}" if event.session else "(no session)"
        )
        # TODO(tui-crows): hand terminal control to tmux from inside Textual.
        # For now, the pane mirror is live and the exact attach command is shown.
        self.notify(f"attach: {hint}", timeout=6)

    def on_plan_list_plan_highlighted(self, event: PlanList.PlanHighlighted) -> None:
        if self._view == "planning":
            self.run_worker(self._render_plan(event.name), exclusive=True)

    def on_plan_list_plan_opened(self, event: PlanList.PlanOpened) -> None:
        self.run_worker(self._open_plan(event.name), exclusive=True)

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

    def action_view_planning(self) -> None:
        if self._insert_if_chat_focused("1"):
            return
        self._set_view("planning")

    def action_view_crows(self) -> None:
        if self._insert_if_chat_focused("2"):
            return
        self._set_view("crows")

    def action_view_schedule(self) -> None:
        if self._insert_if_chat_focused("3"):
            return
        self._set_view("schedule")

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
        self._refresh_db_views()
        if self._view == "planning" and self._plans.selected_name:
            self.run_worker(self._render_plan(self._plans.selected_name), exclusive=True)

    def _apply_mode(self) -> None:
        self._header.set_view(self._view)
        self._grid.display = False
        self._agents.display = self._view == "crows"
        self._plans.display = self._view == "planning"
        self._plan_doc.display = self._view == "planning"
        self._schedule.display = self._view == "schedule"
        self._mirror.display = self._view in {"planning", "crows"}
        self._mirror.styles.width = "34%" if self._view == "planning" else "1fr"

    def on_chat_input_user_message(self, event: ChatInput.UserMessage) -> None:
        self.notify(f"you: {event.text[:90]}", timeout=2)
        if self.orchestrator is None:
            self.notify("chat: no orchestrator attached", severity="error", timeout=3)
            return
        self.run_worker(self._dispatch_chat(event.text), exclusive=False)

    async def _dispatch_chat(self, text: str) -> None:
        if text.startswith("/"):
            await self._handle_slash(text)
            return
        # Serialize ensure+send so a flurry of messages during cold-start
        # doesn't race two collaborator spawns or interleave send_keys.
        async with self._collab_lock:
            already = self.runtime.db and self.runtime.db.execute(
                "SELECT agent_id FROM agents WHERE role='collaborator' "
                "AND status IN ('running','idle') LIMIT 1"
            ).fetchone()
            if not already:
                self.notify("starting collaborator…", timeout=30)
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
            except Exception as e:
                self._record_ui_escalation(f"Collaborator startup failed: {e}")
                self.notify(f"collaborator spawn failed: {e}", severity="error", timeout=10)
                return
            agent = self.runtime.get_agent(agent_id)
            if agent is None:
                self.notify("collaborator vanished after spawn", severity="error", timeout=5)
                return
            # Route pane mirror to collaborator session so user can see it.
            self._mirror.set_session(agent.session)
            self.run_worker(self._mirror.refresh_pane(), exclusive=True)
            try:
                await agent.send(text)
            except Exception as e:
                self.notify(f"send failed: {e}", severity="error", timeout=5)
                return
        self.notify("→ collaborator", timeout=2)

    async def _handle_slash(self, text: str) -> None:
        parts = text[1:].split()
        if not parts:
            return
        cmd, *args = parts
        if cmd == "murder":
            await self._kick_ready()
        else:
            self.notify(f"unknown command: /{cmd}", severity="warning", timeout=3)

    def action_quit(self) -> None:
        if self._insert_if_chat_focused("q"):
            return
        self.exit()

    def action_focus_chat(self) -> None:
        self.set_focus(self._chat)

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

    def _watch_theme(self, theme_name: str) -> None:
        super()._watch_theme(theme_name)
        if not getattr(self, "_persist_theme_changes", False):
            return
        if theme_name not in self.available_themes:
            return
        self._user_config.tui.theme = theme_name
        save_user_config(self._user_config)
