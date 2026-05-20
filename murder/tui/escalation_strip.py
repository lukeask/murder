"""Bottom escalation strip — pending escalations from service snapshots."""

from __future__ import annotations

from textual.binding import Binding
from textual.message import Message
from textual.widgets import Static

from murder.service.client_api import EscalationSummary, EscalationsSnapshot


class EscalationStrip(Static):
    """Compact text-only list. Empty when no escalations are pending."""

    BINDINGS = [
        Binding("r", "retry_latest_failed", "Retry failed escalation", show=False),
        Binding("up", "cursor_up", "Prev escalation", show=False),
        Binding("k", "cursor_up", "Prev escalation", show=False),
        Binding("down", "cursor_down", "Next escalation", show=False),
        Binding("j", "cursor_down", "Next escalation", show=False),
        Binding("enter", "navigate", "Go to escalation", show=False),
    ]

    DEFAULT_CSS = """
    EscalationStrip {
        height: auto;
        max-height: 12;
        border: solid $error;
        padding: 0 1;
    }
    """

    def __init__(self) -> None:
        super().__init__("escalations: (none)")
        self.can_focus = True
        self.border_title = "escalations"
        self._latest_failed_ticket_id: str | None = None
        self._active_rows: list[EscalationSummary] = []
        self._cursor_idx: int = 0

    class RetryRequested(Message):
        def __init__(self, ticket_id: str) -> None:
            super().__init__()
            self.ticket_id = ticket_id

    class NavigateRequested(Message):
        def __init__(self, escalation: EscalationSummary) -> None:
            super().__init__()
            self.escalation = escalation

    def refresh_from_snapshot(
        self,
        snapshot: EscalationsSnapshot,
        *,
        limit: int = 6,
        history_limit: int = 5,
    ) -> None:
        self._active_rows = list(snapshot.active[:limit])
        if self._cursor_idx >= len(self._active_rows):
            self._cursor_idx = max(0, len(self._active_rows) - 1)
        history_rows = snapshot.history[:history_limit]
        if not self._active_rows and not history_rows:
            self.display = False
            return
        self.display = True
        self._latest_failed_ticket_id = None
        lines = []
        for idx, row in enumerate(self._active_rows):
            sev = "!" * int(row.severity)
            tid = row.ticket_id or "-"
            is_failed = row.ticket_status == "failed"
            if is_failed and self._latest_failed_ticket_id is None and row.ticket_id:
                self._latest_failed_ticket_id = row.ticket_id
            retry_hint = " [dim][r retry][/dim]" if is_failed else ""
            enter_hint = " [dim][↵][/dim]" if idx == self._cursor_idx else ""
            prefix = "[b]>[/b] " if idx == self._cursor_idx else "  "
            lines.append(
                f"{prefix}[b]{sev}[/b] #{row.id} → {row.to_recipient} · {tid} · {row.reason}"
                + retry_hint
                + enter_hint
            )
        if history_rows:
            if lines:
                lines.append("")
            lines.append("[dim]— resolved —[/dim]")
            for row in history_rows:
                tid = row.ticket_id or "-"
                lines.append(f"[dim]  #{row.id} {tid} · {row.reason}[/dim]")
        self.update("\n".join(lines))

    def action_cursor_up(self) -> None:
        if self._active_rows:
            self._cursor_idx = (self._cursor_idx - 1) % len(self._active_rows)
            self._re_render()

    def action_cursor_down(self) -> None:
        if self._active_rows:
            self._cursor_idx = (self._cursor_idx + 1) % len(self._active_rows)
            self._re_render()

    def action_navigate(self) -> None:
        if not self._active_rows:
            return
        self.post_message(self.NavigateRequested(self._active_rows[self._cursor_idx]))

    def action_retry_latest_failed(self) -> None:
        if self._latest_failed_ticket_id is None:
            self.app.notify(
                "No failed escalation ticket available to retry.",
                severity="warning",
                timeout=4,
            )
            return
        self.post_message(self.RetryRequested(self._latest_failed_ticket_id))

    def _re_render(self) -> None:
        """Re-render with updated cursor position without a full snapshot refresh."""
        lines = []
        self._latest_failed_ticket_id = None
        for idx, row in enumerate(self._active_rows):
            sev = "!" * int(row.severity)
            tid = row.ticket_id or "-"
            is_failed = row.ticket_status == "failed"
            if is_failed and self._latest_failed_ticket_id is None and row.ticket_id:
                self._latest_failed_ticket_id = row.ticket_id
            retry_hint = " [dim][r retry][/dim]" if is_failed else ""
            enter_hint = " [dim][↵][/dim]" if idx == self._cursor_idx else ""
            prefix = "[b]>[/b] " if idx == self._cursor_idx else "  "
            lines.append(
                f"{prefix}[b]{sev}[/b] #{row.id} → {row.to_recipient} · {tid} · {row.reason}"
                + retry_hint
                + enter_hint
            )
        self.update("\n".join(lines) if lines else "escalations: (none)")
