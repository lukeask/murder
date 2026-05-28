"""Inline wizard for spawning a rogue crow."""

from __future__ import annotations

from collections.abc import Callable

from rich.markup import escape
from textual.app import ComposeResult
from textual.binding import Binding
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Input, Static

from murder.harnesses import REGISTRY, capabilities_for

_HARNESS_MODELS: dict[str, list[str]] = {
    "claude_code": [
        "claude-sonnet-4-6",
        "claude-opus-4-7",
        "claude-haiku-4-5-20251001",
    ],
    "codex": [
        "gpt-5.5",
        "gpt-5.4",
        "gpt-5.3-codex",
    ],
    "pi": [],
    "cursor": [],
    "antigravity": [],
    "native_coding_crow": [],
}

_HARNESS_ORDER = [
    "claude_code",
    "codex",
    "cursor",
    "pi",
    "antigravity",
    "native_coding_crow",
]


def _display_harness(kind: str) -> str:
    return kind.replace("_", "-")


class SpawnWizard(Widget):
    """Inline 3-step harness/model/name selector."""

    DEFAULT_CSS = """
    SpawnWizard {
        height: auto;
        padding: 0 1;
        background: $surface;
        border: solid $primary;
    }
    """

    BINDINGS = [
        Binding("j", "cursor_down", show=False),
        Binding("k", "cursor_up", show=False),
        Binding("down", "cursor_down", show=False),
        Binding("up", "cursor_up", show=False),
        Binding("enter", "confirm_step", show=False),
        Binding("escape", "cancel", show=False),
    ]

    can_focus = True

    class Confirmed(Message):
        def __init__(self, harness: str, model: str, name: str | None) -> None:
            self.harness = harness
            self.model = model
            self.name = name or None
            super().__init__()

    class Cancelled(Message):
        pass

    def __init__(self) -> None:
        super().__init__()
        self._step = 0
        self._harnesses: list[str] = []
        self._cursor = 0
        self._selected_harness: str | None = None
        self._selected_model: str | None = None
        self._display = Static("", markup=True)
        self._name_input = Input(placeholder="blank = autogenerate")

    def compose(self) -> ComposeResult:
        yield self._display
        yield self._name_input

    def on_mount(self) -> None:
        known = set(REGISTRY.keys())
        self._harnesses = [kind for kind in _HARNESS_ORDER if kind in known]
        self._harnesses.extend(kind for kind in REGISTRY.keys() if kind not in _HARNESS_ORDER)
        self._refresh_display()

    def action_cursor_down(self) -> None:
        if not self._current_options():
            return
        self._cursor = min(self._cursor + 1, len(self._current_options()) - 1)
        self._refresh_display()

    def action_cursor_up(self) -> None:
        if not self._current_options():
            return
        self._cursor = max(self._cursor - 1, 0)
        self._refresh_display()

    def action_confirm_step(self) -> None:
        if self._step == 0:
            if not self._harnesses:
                return
            self._selected_harness = self._harnesses[self._cursor]
            self._selected_model = ""
            self._cursor = 0
            self._step = 1 if self._should_select_model(self._selected_harness) else 2
            self._refresh_display()
            return

        if self._step == 1:
            models = self._current_models()
            if not models:
                return
            self._selected_model = models[self._cursor]
            self._cursor = 0
            self._step = 2
            self._refresh_display()
            return

        if self._selected_harness is None:
            return
        self._confirm_name()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input is self._name_input and self._step == 2:
            event.stop()
            self._confirm_name()

    def action_cancel(self) -> None:
        self.post_message(self.Cancelled())

    def _confirm_name(self) -> None:
        if self._selected_harness is None:
            return
        self.post_message(
            self.Confirmed(
                self._selected_harness,
                self._selected_model or "",
                self._name_input.value,
            )
        )

    def _refresh_display(self) -> None:
        if self._step == 2:
            self._display.display = False
            self._name_input.display = True
            self._name_input.focus()
            return

        self._display.display = True
        self._name_input.display = False

        if self._step == 0:
            self._display.update(self._format_step("Step 1/3: Select harness", self._harnesses, _display_harness))
            return

        harness = self._selected_harness or ""
        header = f"Step 2/3: Select model  (harness: {_display_harness(harness)})"
        self._display.update(self._format_step(header, self._current_models()))

    def _format_step(
        self,
        header: str,
        options: list[str],
        display_name: Callable[[str], str] = lambda value: value,
    ) -> str:
        lines = [escape(header)]
        for idx, option in enumerate(options):
            name = escape(display_name(option))
            if idx == self._cursor:
                lines.append(f"[bold reverse]> {name}[/]")
            else:
                lines.append(f"  {name}")
        return "\n".join(lines)

    def _current_options(self) -> list[str]:
        if self._step == 0:
            return self._harnesses
        if self._step == 1:
            return self._current_models()
        return []

    def _current_models(self) -> list[str]:
        if self._selected_harness is None:
            return []
        return _HARNESS_MODELS.get(self._selected_harness, [])

    def _should_select_model(self, harness: str) -> bool:
        if not capabilities_for(harness).model_selection:
            return False
        return bool(_HARNESS_MODELS.get(harness))
