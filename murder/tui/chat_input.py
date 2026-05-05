"""Chat input box.

Submissions are routed by the parent app to the Collaborator session
via the Orchestrator (lazy-spawn on first message).

Enter submits; Shift+Enter inserts a newline."""

from __future__ import annotations

from textual.events import Key
from textual.message import Message
from textual.widgets import TextArea


class ChatInput(TextArea):
    class UserMessage(Message):
        def __init__(self, text: str) -> None:
            self.text = text
            super().__init__()

    DEFAULT_CSS = """
    ChatInput {
        border: round $accent;
        height: auto;
        min-height: 3;
        max-height: 8;
    }
    ChatInput:focus {
        border: heavy $primary;
    }
    """

    def __init__(self) -> None:
        super().__init__()

    def on_mount(self) -> None:
        self.border_title = "collaborator"
        # TODO(tui-planning): add explicit collaborator persona switching
        # between planner and notetaker. Sentinel and Augur are separate roles.
        self.border_subtitle = "Enter to send · Shift+Enter for newline"

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
