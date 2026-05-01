"""Chat input box.

Submissions are routed by the parent app to the Collaborator session
via the Orchestrator (lazy-spawn on first message)."""

from __future__ import annotations

from textual.message import Message
from textual.widgets import Input


class ChatInput(Input):
    class Submitted(Message):
        def __init__(self, text: str) -> None:
            self.text = text
            super().__init__()

    DEFAULT_CSS = """
    ChatInput {
        border: round $accent;
        height: 3;
    }
    """

    def __init__(self) -> None:
        super().__init__(placeholder="chat with the collaborator (/murder to kick off ready tickets)")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        self.value = ""
        self.post_message(self.Submitted(text))
