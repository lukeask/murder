"""Scheduler mode strip — shows current mode with an inline picker."""

from __future__ import annotations

import sqlite3

from textual.app import ComposeResult
from textual.binding import Binding
from textual.message import Message
from textual.widgets import Static

_MODES = ("manual", "autorun_ready", "crow_magic")
_MODE_LABELS = {
    "manual": "Manual",
    "autorun_ready": "Autorun Ready",
    "crow_magic": "Crow Magic",
}


class ModeStrip(Static):
    """Renders the current scheduler mode with picker interactions."""

    BINDINGS = [
        Binding("m", "open_mode_picker", "Mode"),
        Binding("left", "picker_left", show=False),
        Binding("right", "picker_right", show=False),
        Binding("enter", "picker_confirm", show=False),
        Binding("escape", "picker_cancel", show=False),
    ]

    DEFAULT_CSS = """
    ModeStrip {
        height: 2;
        color: $text-muted;
    }
    """

    class SetModeRequested(Message):
        def __init__(self, to_mode: str) -> None:
            super().__init__()
            self.to_mode = to_mode

    def __init__(self) -> None:
        super().__init__("")
        self._mode = "manual"
        self._rationale = ""
        self._picker_open = False
        self._picker_index = 0

    def on_mount(self) -> None:
        self._render_mode()

    def refresh_from_db(self, db: sqlite3.Connection | None) -> None:
        if db is None:
            return
        row = db.execute(
            "SELECT mode FROM scheduler_state WHERE id = 1"
        ).fetchone()
        if row is not None:
            self._mode = row["mode"]
        if self._mode == "crow_magic":
            rat_row = db.execute(
                "SELECT rationale FROM scheduler_decision_cache ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
            self._rationale = rat_row["rationale"] if rat_row is not None else ""
        else:
            self._rationale = ""
        self._render_mode()

    def _render_mode(self) -> None:
        label = _MODE_LABELS.get(self._mode, self._mode)
        if self._picker_open:
            choices: list[str] = []
            for i, mode in enumerate(_MODES):
                mode_label = _MODE_LABELS.get(mode, mode)
                if i == self._picker_index:
                    choices.append(f"[b][reverse]{mode_label}[/reverse][/b]")
                else:
                    choices.append(mode_label)
            line1 = (
                "Scheduler: "
                f"[b]{label}[/b]  [dim]m[/dim] picker  "
                + "  ".join(choices)
                + "  [dim]←/→ move · enter confirm · esc cancel[/dim]"
            )
        else:
            line1 = f"Scheduler: [b]{label}[/b]  [dim]m[/dim] to change"
        if self._rationale:
            line2 = f"[dim]{self._rationale}[/dim]"
            self.update(f"{line1}\n{line2}")
        else:
            self.update(line1)

    def action_open_mode_picker(self) -> None:
        self._picker_open = True
        self._picker_index = _MODES.index(self._mode) if self._mode in _MODES else 0
        self._render_mode()

    def action_picker_left(self) -> None:
        if not self._picker_open:
            return
        self._picker_index = (self._picker_index - 1) % len(_MODES)
        self._render_mode()

    def action_picker_right(self) -> None:
        if not self._picker_open:
            return
        self._picker_index = (self._picker_index + 1) % len(_MODES)
        self._render_mode()

    def action_picker_confirm(self) -> None:
        if not self._picker_open:
            return
        next_mode = _MODES[self._picker_index]
        self._picker_open = False
        self._render_mode()
        self.post_message(self.SetModeRequested(next_mode))

    def action_picker_cancel(self) -> None:
        if not self._picker_open:
            return
        self._picker_open = False
        self._render_mode()
