"""Chat input box.

Submissions are routed by the parent app to the Collaborator session
via the Orchestrator (lazy-spawn on first message).

Enter submits; Shift+Enter inserts a newline."""

from __future__ import annotations

from textual.binding import Binding
from textual.events import Key
from textual.message import Message
from textual.widgets import TextArea


class ChatInput(TextArea):
    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
    ]
    class UserMessage(Message):
        def __init__(self, text: str) -> None:
            self.text = text
            super().__init__()

    DEFAULT_CSS = """
    ChatInput {
        border: solid $border;
        height: auto;
        min-height: 5;
        max-height: 8;
    }
    ChatInput:focus {
        border: solid $primary;
    }
    """

    def __init__(self) -> None:
        super().__init__()

    def on_mount(self) -> None:
        self.set_recipient("collaborator")
        self.border_subtitle = "Enter to send · Shift+Enter for newline"

    def set_recipient(self, recipient: str) -> None:
        """Update the chat target label shown in the input border."""
        label = recipient.strip() or "collaborator"
        self.border_title = label

    def on_key(self, event: Key) -> None:
        if event.key == "enter":
            event.prevent_default()
            event.stop()
            text = self.text.strip()
            if text:
                self.clear()
                self.post_message(self.UserMessage(text))
        elif event.key == "shift+enter":
            event.prevent_default()
            event.stop()
            self.insert("\n")
        elif event.key == "escape":
            event.prevent_default()
            event.stop()
            self.app.action_restore_focus()
