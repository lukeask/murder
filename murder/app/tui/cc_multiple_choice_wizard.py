"""Inline wizard for a live multiple-choice transcript prompt."""

from __future__ import annotations

from rich.markup import escape
from textual.app import ComposeResult
from textual.binding import Binding
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Static

from murder.llm.harnesses.choice_prompt import MultipleChoicePrompt


class CCMultipleChoiceWizard(Widget):
    """Inline option picker matching the existing wizard interaction model."""

    DEFAULT_CSS = """
    CCMultipleChoiceWizard {
        height: auto;
        padding: 0 1;
        background: $surface;
        border: solid $accent;
    }
    """

    BINDINGS = [
        Binding("j", "cursor_down", show=False),
        Binding("k", "cursor_up", show=False),
        Binding("down", "cursor_down", show=False),
        Binding("up", "cursor_up", show=False),
        Binding("enter", "confirm", show=False),
        Binding("escape", "cancel", show=False),
        Binding("1", "select_digit(1)", show=False),
        Binding("2", "select_digit(2)", show=False),
        Binding("3", "select_digit(3)", show=False),
        Binding("4", "select_digit(4)", show=False),
        Binding("5", "select_digit(5)", show=False),
        Binding("6", "select_digit(6)", show=False),
        Binding("7", "select_digit(7)", show=False),
        Binding("8", "select_digit(8)", show=False),
        Binding("9", "select_digit(9)", show=False),
    ]

    can_focus = True

    class Confirmed(Message):
        def __init__(self, option_number: int, label: str) -> None:
            self.option_number = option_number
            self.label = label
            super().__init__()

    class Cancelled(Message):
        pass

    def __init__(self, prompt: MultipleChoicePrompt) -> None:
        super().__init__()
        self._prompt = prompt
        self._cursor = prompt.selected_index
        self._display = Static("", markup=True)

    @property
    def prompt(self) -> MultipleChoicePrompt:
        return self._prompt

    def compose(self) -> ComposeResult:
        yield self._display

    def on_mount(self) -> None:
        self._refresh_display()

    def update_prompt(self, prompt: MultipleChoicePrompt) -> None:
        self._prompt = prompt
        self._cursor = max(0, min(prompt.selected_index, len(prompt.options) - 1))
        self._refresh_display()

    def action_cursor_down(self) -> None:
        self._cursor = min(self._cursor + 1, len(self._prompt.options) - 1)
        self._refresh_display()

    def action_cursor_up(self) -> None:
        self._cursor = max(self._cursor - 1, 0)
        self._refresh_display()

    def action_confirm(self) -> None:
        option = self._prompt.options[self._cursor]
        self.post_message(self.Confirmed(option.number, option.label))

    def action_cancel(self) -> None:
        self.post_message(self.Cancelled())

    def action_select_digit(self, digit: int) -> None:
        for idx, option in enumerate(self._prompt.options):
            if option.number != digit:
                continue
            self._cursor = idx
            self._refresh_display()
            self.post_message(self.Confirmed(option.number, option.label))
            return

    def _refresh_display(self) -> None:
        lines: list[str] = []
        if self._prompt.question:
            lines.append(escape(self._prompt.question))
            lines.append("")
        for idx, option in enumerate(self._prompt.options):
            label = escape(f"{option.number}. {option.label}")
            prefix = "❯" if idx == self._cursor else " "
            style = "[bold reverse]" if idx == self._cursor else ""
            suffix = "[/]" if idx == self._cursor else ""
            lines.append(f"{style}{prefix} {label}{suffix}")
            if option.description:
                lines.append(f"[dim]    {escape(option.description)}[/]")
        if self._prompt.footer:
            lines.append("")
            lines.append(f"[dim]{escape(self._prompt.footer)}[/]")
        self._display.update("\n".join(lines))


__all__ = ["CCMultipleChoiceWizard"]
