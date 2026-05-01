"""Top-level Textual app — wires header, ticket grid, pane mirror, and
escalation strip onto the running Runtime."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer

from murder.tui.chat_input import ChatInput
from murder.tui.escalation_strip import EscalationStrip
from murder.tui.header import Header
from murder.tui.pane_mirror import PaneMirror
from murder.tui.ticket_grid import TicketGrid

if TYPE_CHECKING:
    from murder.orchestrator import Orchestrator
    from murder.runtime import Runtime


class MurderApp(App[None]):
    """Single-screen TUI: header / [grid | mirror] / escalations / chat."""

    TITLE = "murder"
    BINDINGS = [
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
    """

    def __init__(self, runtime: "Runtime", orchestrator: "Orchestrator | None" = None) -> None:
        super().__init__()
        self.runtime = runtime
        self.orchestrator = orchestrator
        self._header = Header(runtime.config.project.name)
        self._grid = TicketGrid()
        self._mirror = PaneMirror()
        self._escalations = EscalationStrip()
        self._chat = ChatInput()
        self._collab_lock = asyncio.Lock()

    def compose(self) -> ComposeResult:
        yield self._header
        with Horizontal(id="body"):
            yield self._grid
            yield self._mirror
        yield self._escalations
        yield self._chat
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = str(self.runtime.repo_root)
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
        self._escalations.refresh_from_db(db)

    async def _refresh_pane(self) -> None:
        await self._mirror.refresh_pane()

    def on_ticket_grid_ticket_selected(self, event: TicketGrid.TicketSelected) -> None:
        monkey = self.runtime.get_monkey(event.ticket_id)
        session = monkey.session if monkey is not None else None
        self._mirror.set_session(session)
        self.run_worker(self._mirror.refresh_pane(), exclusive=True)

    def on_chat_input_submitted(self, event: ChatInput.Submitted) -> None:
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
            try:
                agent_id = await self.orchestrator.ensure_collaborator()
            except Exception as e:
                self.notify(f"collaborator spawn failed: {e}", severity="error", timeout=5)
                return
            agent = self.runtime.get_agent(agent_id)
            if agent is None:
                self.notify("collaborator vanished after spawn", severity="error", timeout=5)
                return
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
