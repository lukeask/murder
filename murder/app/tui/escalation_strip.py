"""Bottom escalation strip — pending escalations from service snapshots.

Phase 2 (t055): EscalationStrip is now a StoreComponent.  When a store is
bound (by t056's layout module), on_mount subscribes and refresh_from_snapshot
is called automatically on every state change.  Until t056 binds the store the
bridge in app.py continues to call refresh_from_snapshot directly — both paths
are safe because refresh_from_snapshot is idempotent.

The snapshot type accepted by refresh_from_snapshot is deliberately kept as the
duck-typed union of EscalationsSnapshot (client_api) and EscalationsStoreSnapshot
(stores.escalations) — both expose .active and .history tuples of EscalationSummary,
so no isinstance check is needed.  Store-side derivation (active-row limit,
display-text) is deferred to t056 / whoever owns stores/escalations.py.
"""

from __future__ import annotations

from textual.binding import Binding
from textual.message import Message
from textual.widgets import Static

from murder.app.service.client_api import EscalationsSnapshot, EscalationSummary
from murder.app.tui.components import StoreComponent


class EscalationStrip(StoreComponent, Static):
    """Compact text-only list. Empty when no escalations are pending.

    Inherits StoreComponent so it self-subscribes when a store is bound.
    Falls back to the bridge (app.py calls refresh_from_snapshot directly) when
    no store is bound — which is the case until t056.

    View-local state: _cursor_idx, _active_rows, _user_visible.
    """

    BINDINGS = [
        Binding("r", "retry_latest_failed", "Retry failed escalation", show=False),
        Binding("a", "ack", "Resolve", show=False),
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
        # View-local visibility flag preserved across store-driven re-renders.
        self._user_visible: bool = True

    class AckRequested(Message):
        def __init__(self, escalation: EscalationSummary) -> None:
            super().__init__()
            self.escalation = escalation
            self.escalation_id = escalation.id

    class RetryRequested(Message):
        def __init__(self, ticket_id: str) -> None:
            super().__init__()
            self.ticket_id = ticket_id

    class NavigateRequested(Message):
        def __init__(self, escalation: EscalationSummary) -> None:
            super().__init__()
            self.escalation = escalation

    def on_mount(self) -> None:
        """Subscribe to bound store (if any) and do the initial paint.

        Calls super().on_mount() which is StoreComponent.on_mount() → sets up
        subscriptions when a store has been bound via bind_stores().  When no
        store is bound this is a no-op and the bridge path stays active.
        """
        super().on_mount()

    def refresh_from_snapshot(
        self,
        snapshot: EscalationsSnapshot,
        *,
        limit: int = 6,
        show: bool | None = None,
    ) -> None:
        """Render from snapshot.

        ``show`` controls visibility:
        - When called from the bridge (app.py), ``show`` is passed explicitly.
          We honour it AND persist it so subsequent store-driven re-renders
          respect the user's choice.
        - When called from the store path (no explicit ``show``), we use the
          persisted ``_user_visible`` flag.
        """
        if show is not None:
            self._user_visible = show
        effective_show = self._user_visible
        self._active_rows = list(snapshot.active[:limit])
        if self._cursor_idx >= len(self._active_rows):
            self._cursor_idx = max(0, len(self._active_rows) - 1)
        if not self._active_rows:
            self._latest_failed_ticket_id = None
            self.display = False
            self.update("escalations: (none)")
            return
        self._sync_display(show=effective_show)
        self._render_rows()

    def set_user_visible(self, visible: bool) -> None:
        """Show or hide the strip without dropping cached active rows."""
        self._user_visible = visible
        self._sync_display(show=visible)

    def _sync_display(self, *, show: bool) -> None:
        self.display = show and bool(self._active_rows)

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

    def action_ack(self) -> None:
        if not self._active_rows:
            return
        self.post_message(self.AckRequested(self._active_rows[self._cursor_idx]))

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
        self._render_rows()

    def _render_rows(self) -> None:
        lines = []
        self._latest_failed_ticket_id = None
        for idx, row in enumerate(self._active_rows):
            sev = "!" * int(row.severity)
            tid = row.ticket_id or "-"
            is_failed = row.ticket_status == "failed"
            if is_failed and self._latest_failed_ticket_id is None and row.ticket_id:
                self._latest_failed_ticket_id = row.ticket_id
            retry_hint = " [dim]\\[r retry][/dim]" if is_failed else ""
            action_hint = " [dim]\\[a solve] [↵][/dim]" if idx == self._cursor_idx else ""
            prefix = "[b]>[/b] " if idx == self._cursor_idx else "  "
            lines.append(
                f"{prefix}[b]{sev}[/b] #{row.id} → {row.to_recipient} · {tid} · {row.reason}"
                + retry_hint
                + action_hint
            )
        self.update("\n".join(lines) if lines else "escalations: (none)")
