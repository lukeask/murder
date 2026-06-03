"""Chat input box.

Submissions are routed by the parent app to the Collaborator session
via the Orchestrator (lazy-spawn on first message).

Enter submits; Shift+Enter inserts a newline. Up/Down recall prior sent
messages (readline-style draft is preserved). :raw forwards keys to
the harness until Esc Esc. For a crow target, Enter on an empty box
requests an interrupt (flush a queued message sooner)."""

from __future__ import annotations

import time

from textual.binding import Binding
from textual.events import Key
from textual.message import Message
from textual.widgets import TextArea

_SENT_HISTORY_MAX = 200
_RAW_KEY_ESCAPE_EXIT_S = 0.45
_SPAWN_COMMANDS = frozenset({":spawn", ":s"})

_NAMED_TMUX_KEYS: dict[str, str] = {
    "enter": "Enter",
    "tab": "Tab",
    "backspace": "BSpace",
    "delete": "Delete",
    "escape": "Escape",
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
    "home": "Home",
    "end": "End",
    "pageup": "PageUp",
    "pagedown": "PageDown",
    "space": "Space",
}


class _SentMessageHistory:
    """In-memory sent-message stack for Up/Down recall in the chat box."""

    def __init__(self, *, maxlen: int = _SENT_HISTORY_MAX) -> None:
        self._entries: list[str] = []
        self._maxlen = maxlen
        self._index = 0
        self._draft = ""

    def append(self, text: str) -> None:
        self._entries.append(text)
        if len(self._entries) > self._maxlen:
            del self._entries[: len(self._entries) - self._maxlen]
        self._index = len(self._entries)
        self._draft = ""

    def browse_up(self, current: str) -> str | None:
        if not self._entries:
            return None
        if self._index == len(self._entries):
            self._draft = current
        if self._index > 0:
            self._index -= 1
        return self._entries[self._index]

    def browse_down(self) -> str | None:
        if not self._entries or self._index >= len(self._entries):
            return None
        self._index += 1
        if self._index == len(self._entries):
            return self._draft
        return self._entries[self._index]


def _harness_delivery(event: Key) -> tuple[str, bool] | None:
    """Map a Textual key event to (tmux payload, literal flag)."""
    character = getattr(event, "character", None)
    if character is not None and len(character) == 1 and event.is_printable:
        return (character, True)
    key = event.key
    named = _NAMED_TMUX_KEYS.get(key)
    if named is not None:
        return (named, False)
    if key.startswith("ctrl+"):
        part = key[5:]
        if len(part) == 1:
            return (f"C-{part.lower()}", False)
        if part == "space":
            return ("C-Space", False)
    return None


class ChatInput(TextArea):
    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
    ]

    class UserMessage(Message):
        def __init__(self, text: str) -> None:
            self.text = text
            super().__init__()

    class EmptySubmit(Message):
        """Enter pressed with no text (crow interrupt when the app routes to a crow)."""

    class SpawnCommand(Message):
        """Literal :spawn or :s submitted; the app mounts the inline spawn wizard."""

    class RawKeyPress(Message):
        def __init__(self, key: str, *, literal: bool = False) -> None:
            self.key = key
            self.literal = literal
            super().__init__()

    class RawKeyModeExit(Message):
        """Esc Esc — leave raw key mode."""

    DEFAULT_CSS = """
    ChatInput {
        border: solid $border;
        height: auto;
        min-height: 5;
        max-height: 8;
    }
    ChatInput:focus {
        border: heavy $accent;
        background-tint: 0%;
    }
    ChatInput.-raw-key-mode {
        border: solid $warning;
    }
    ChatInput.-raw-key-mode:focus {
        border: solid $warning;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._sent_history = _SentMessageHistory()
        self._raw_key_mode = False
        self._escape_armed_at = 0.0

    def on_mount(self) -> None:
        self.set_recipient("collaborator")
        self._refresh_hints()

    @property
    def raw_key_mode(self) -> bool:
        return self._raw_key_mode

    def set_raw_key_mode(self, active: bool) -> None:
        self._raw_key_mode = active
        self._escape_armed_at = 0.0
        self.set_class(active, "-raw-key-mode")
        if active:
            self.clear()
            self.focus()
        self._refresh_hints()

    def _refresh_hints(self, *, pending: str | None = None) -> None:
        if self._raw_key_mode:
            hints = "RAW KEY MODE · Esc Esc to exit · keys → harness"
        else:
            hints = "Enter to send · ↑↓ history · Shift+Enter for newline"
            if getattr(self, "_is_crow_target", False):
                hints = f"Enter on empty = interrupt · {hints}"
            if pending:
                preview = pending if len(pending) <= 48 else f"{pending[:45]}…"
                hints = f"pending: {preview} · {hints}"
        self.border_subtitle = hints

    def set_recipient(self, recipient: str, *, is_crow: bool = False) -> None:
        """Update the chat target label shown in the input border."""
        label = recipient.strip() or "collaborator"
        self.border_title = label
        self._is_crow_target = is_crow
        self._refresh_hints()

    def set_pending(self, text: str | None) -> None:
        self._refresh_hints(pending=text)

    def _set_input_text(self, value: str) -> None:
        self.text = value
        lines = value.split("\n")
        if not lines:
            return
        self.move_cursor((len(lines) - 1, len(lines[-1])))

    def _exit_raw_key_mode(self) -> None:
        if not self._raw_key_mode:
            return
        self.set_raw_key_mode(False)
        self.post_message(self.RawKeyModeExit())

    def _handle_raw_key(self, event: Key) -> None:
        event.prevent_default()
        event.stop()
        if event.key == "escape":
            now = time.monotonic()
            if self._escape_armed_at and now - self._escape_armed_at <= _RAW_KEY_ESCAPE_EXIT_S:
                self._exit_raw_key_mode()
                return
            self._escape_armed_at = now
            self.post_message(self.RawKeyPress("Escape", literal=False))
            return
        self._escape_armed_at = 0.0
        delivery = _harness_delivery(event)
        if delivery is None:
            return
        key, literal = delivery
        self.post_message(self.RawKeyPress(key, literal=literal))

    def on_key(self, event: Key) -> None:
        if self._raw_key_mode:
            self._handle_raw_key(event)
            return
        if event.key == "enter":
            event.prevent_default()
            event.stop()
            text = self.text.strip()
            if text in _SPAWN_COMMANDS:
                self.clear()
                self.post_message(self.SpawnCommand())
            elif text:
                self._sent_history.append(text)
                self.clear()
                self.post_message(self.UserMessage(text))
            elif getattr(self, "_is_crow_target", False):
                self.post_message(self.EmptySubmit())
        elif event.key == "shift+enter":
            event.prevent_default()
            event.stop()
            self.insert("\n")
        elif event.key == "up":
            event.prevent_default()
            event.stop()
            recalled = self._sent_history.browse_up(self.text)
            if recalled is not None:
                self._set_input_text(recalled)
        elif event.key == "down":
            event.prevent_default()
            event.stop()
            recalled = self._sent_history.browse_down()
            if recalled is not None:
                self._set_input_text(recalled)
        elif event.key == "ctrl+d":
            event.prevent_default()
            event.stop()
            if hasattr(self.app, "_chat_input_memory"):
                self.app._chat_input_memory = self.text
            self.clear()
        elif event.key == "ctrl+p":
            event.prevent_default()
            event.stop()
            memory = getattr(self.app, "_chat_input_memory", "")
            if memory:
                self._set_input_text(memory)
        elif event.key == "escape":
            event.prevent_default()
            event.stop()
            self.app.action_restore_focus()
