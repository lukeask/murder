"""Inline wizard for resolving the selected escalation."""

from __future__ import annotations

from dataclasses import dataclass

from rich.markup import escape
from textual.app import ComposeResult
from textual.binding import Binding
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Static

from murder.app.service.client_api import EscalationSummary


@dataclass(frozen=True, slots=True)
class EscalationResolveAction:
    key: str
    label: str


class EscalationResolveWizard(Widget):
    """Inline action picker matching the current :spawn wizard interaction model."""

    DEFAULT_CSS = """
    EscalationResolveWizard {
        height: auto;
        padding: 0 1;
        background: $surface;
        border: solid $error;
    }
    """

    BINDINGS = [
        Binding("j", "cursor_down", show=False),
        Binding("k", "cursor_up", show=False),
        Binding("down", "cursor_down", show=False),
        Binding("up", "cursor_up", show=False),
        Binding("enter", "confirm", show=False),
        Binding("escape", "cancel", show=False),
    ]

    can_focus = True

    class Confirmed(Message):
        def __init__(self, escalation: EscalationSummary, action: str) -> None:
            self.escalation = escalation
            self.action = action
            super().__init__()

    class Cancelled(Message):
        pass

    def __init__(self, escalation: EscalationSummary) -> None:
        super().__init__()
        self.escalation = escalation
        self._cursor = 0
        self._display = Static("", markup=True)
        self._actions = self._build_actions(escalation)

    def compose(self) -> ComposeResult:
        yield self._display

    def on_mount(self) -> None:
        self._refresh_display()

    def action_cursor_down(self) -> None:
        if not self._actions:
            return
        self._cursor = min(self._cursor + 1, len(self._actions) - 1)
        self._refresh_display()

    def action_cursor_up(self) -> None:
        if not self._actions:
            return
        self._cursor = max(self._cursor - 1, 0)
        self._refresh_display()

    def action_confirm(self) -> None:
        if not self._actions:
            return
        self.post_message(self.Confirmed(self.escalation, self._actions[self._cursor].key))

    def action_cancel(self) -> None:
        self.post_message(self.Cancelled())

    def _refresh_display(self) -> None:
        row = self.escalation
        ticket = row.ticket_id or "-"
        header = f"Resolve escalation #{row.id}  ticket: {ticket}"
        lines = [
            escape(header),
            escape(row.reason),
            "",
        ]
        for idx, action in enumerate(self._actions):
            label = escape(action.label)
            if idx == self._cursor:
                lines.append(f"[bold reverse]> {label}[/]")
            else:
                lines.append(f"  {label}")
        lines.append("")
        lines.append("[dim]Enter confirms · Esc cancels[/dim]")
        self._display.update("\n".join(lines))

    def _build_actions(self, escalation: EscalationSummary) -> list[EscalationResolveAction]:
        actions = [
            EscalationResolveAction("ack", "Acknowledge / mark resolved"),
        ]
        if escalation.ticket_status == "failed" and escalation.ticket_id:
            actions.append(
                EscalationResolveAction("retry_ack", "Retry failed ticket and mark resolved")
            )
        actions.append(EscalationResolveAction("navigate", "Open affected view"))
        return actions
