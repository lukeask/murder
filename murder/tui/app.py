"""Top-level Textual app — wires header, ticket grid, pane mirror, and
escalation strip onto the running Runtime."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import Footer

from murder.tui.chat_input import ChatInput
from murder.tui.escalation_strip import EscalationStrip
from murder.tui.header import Header
from murder.tui.pane_mirror import PaneMirror
from murder.tui.plan_view import PlanDocument, PlanList
from murder.tui.themes import CUSTOM_THEMES
from murder.tui.ticket_grid import TicketGrid
from murder.user_config import UserConfig, load_user_config, save_user_config

if TYPE_CHECKING:
    from murder.orchestrator import Orchestrator
    from murder.runtime import Runtime


class MurderApp(App[None]):
    """Single-screen TUI: header / [grid | mirror] / escalations / chat."""

    TITLE = "murder"
    BINDINGS = [
        ("ctrl+p", "change_theme", "Theme"),
        ("p", "toggle_planning", "Planning"),
        ("q", "quit", "Quit"),
        ("r", "refresh_now", "Refresh"),
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
    PlanList {
        width: 32%;
        border: round $accent;
    }
    """

    def __init__(self, runtime: Runtime, orchestrator: Orchestrator | None = None) -> None:
        super().__init__()
        self.runtime = runtime
        self.orchestrator = orchestrator
        self._header = Header(runtime.config.project.name)
        self._grid = TicketGrid()
        self._plans = PlanList()
        self._plan_doc = PlanDocument()
        self._mirror = PaneMirror()
        self._escalations = EscalationStrip()
        self._chat = ChatInput()
        self._collab_lock = asyncio.Lock()
        self._user_config: UserConfig = load_user_config()
        self._persist_theme_changes = False
        self._planning_mode = False
        for theme in CUSTOM_THEMES:
            self.register_theme(theme)

    def compose(self) -> ComposeResult:
        yield self._header
        with Horizontal(id="body"):
            yield self._grid
            yield self._plans
            yield self._plan_doc
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
        interval_s = max(self.runtime.config.tui.refresh_ms, 250) / 1000
        self.set_interval(interval_s, self._refresh_db_views)
        # Pane mirror cadence is independent — capture-pane is cheap but adds
        # tmux load if the user pegs refresh_ms low.
        self.set_interval(max(interval_s, 1.0), self._refresh_pane)

    def action_refresh_now(self) -> None:
        self._refresh_db_views()
        self.run_worker(self._mirror.refresh_pane(), exclusive=True)

    def _refresh_db_views(self) -> None:
        db = self.runtime.db
        self._header.refresh_counts(db)
        self._grid.refresh_from_db(db)
        self._plans.refresh_from_db(db)
        self._escalations.refresh_from_db(db)
        if self._planning_mode and self._plans.selected_name:
            self.run_worker(self._render_plan(self._plans.selected_name), exclusive=True)

    async def _refresh_pane(self) -> None:
        await self._mirror.refresh_pane()

    def on_ticket_grid_ticket_selected(self, event: TicketGrid.TicketSelected) -> None:
        monkey = self.runtime.get_monkey(event.ticket_id)
        session = monkey.session if monkey is not None else None
        self._mirror.set_session(session)
        self.run_worker(self._mirror.refresh_pane(), exclusive=True)

    def on_plan_list_plan_highlighted(self, event: PlanList.PlanHighlighted) -> None:
        if self._planning_mode:
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

    def action_toggle_planning(self) -> None:
        self._planning_mode = not self._planning_mode
        self._apply_mode()
        self._refresh_db_views()
        if self._planning_mode and self._plans.selected_name:
            self.run_worker(self._render_plan(self._plans.selected_name), exclusive=True)

    def _apply_mode(self) -> None:
        self._grid.display = not self._planning_mode
        self._plans.display = self._planning_mode
        self._plan_doc.display = self._planning_mode
        self._mirror.display = True
        self._mirror.styles.width = "34%" if self._planning_mode else "1fr"

    def on_chat_input_user_message(self, event: ChatInput.UserMessage) -> None:
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
                agent_id = await self.orchestrator.ensure_collaborator()
            except Exception as e:
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
        else:
            self.notify(f"unknown command: /{cmd}", severity="warning", timeout=3)

    def action_quit(self) -> None:
        self.exit()

    def _watch_theme(self, theme_name: str) -> None:
        super()._watch_theme(theme_name)
        if not getattr(self, "_persist_theme_changes", False):
            return
        if theme_name not in self.available_themes:
            return
        self._user_config.tui.theme = theme_name
        save_user_config(self._user_config)
