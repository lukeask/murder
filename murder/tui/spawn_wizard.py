"""Inline wizard for spawning a rogue crow."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from rich.markup import escape
from textual.app import ComposeResult
from textual.binding import Binding
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Input, Static

from murder.harnesses import REGISTRY, capabilities_for
from murder.storage.worktrees import WorktreeEntry

_HARNESS_MODELS: dict[str, list[str]] = {
    "claude_code": [
        "sonnet",
        "opus",
        "haiku",
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

_MAIN_WORKTREE = "__main__"
_NEW_WORKTREE = "__new__"


def _display_harness(kind: str) -> str:
    return kind.replace("_", "-")


@dataclass(frozen=True, slots=True)
class WorktreeOption:
    key: str
    label: str


def build_worktree_options(
    repo_root: Path,
    entries: list[WorktreeEntry],
) -> list[WorktreeOption]:
    options = [
        WorktreeOption(_MAIN_WORKTREE, f"main checkout ({repo_root})"),
    ]
    for entry in entries:
        if entry.is_main:
            continue
        branch = entry.branch or entry.path.name
        options.append(
            WorktreeOption(str(entry.path), f"{branch} ({entry.path})"),
        )
    options.append(WorktreeOption(_NEW_WORKTREE, "+ new worktree"))
    return options


class SpawnWizard(Widget):
    """Inline harness/model/worktree/name selector for rogue crows."""

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
        def __init__(
            self,
            harness: str,
            model: str,
            name: str | None,
            *,
            worktree_path: str | None = None,
            worktree_branch: str | None = None,
        ) -> None:
            self.harness = harness
            self.model = model
            self.name = name or None
            self.worktree_path = worktree_path
            self.worktree_branch = worktree_branch
            super().__init__()

    class Cancelled(Message):
        pass

    def __init__(self, *, worktree_options: list[WorktreeOption] | None = None) -> None:
        super().__init__()
        self._worktree_options = list(worktree_options or [])
        self._phase = "harness"
        self._harnesses: list[str] = []
        self._cursor = 0
        self._selected_harness: str | None = None
        self._selected_model: str | None = None
        self._selected_worktree_key: str | None = None
        self._display = Static("", markup=True)
        self._branch_input = Input(placeholder="branch name, e.g. feature/my-work")
        self._name_input = Input(placeholder="blank = autogenerate")

    def compose(self) -> ComposeResult:
        yield self._display
        yield self._branch_input
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
        if self._phase == "harness":
            if not self._harnesses:
                return
            self._selected_harness = self._harnesses[self._cursor]
            self._selected_model = ""
            self._cursor = 0
            self._phase = (
                "model"
                if self._should_select_model(self._selected_harness)
                else ("worktree" if self._worktree_options else "name")
            )
            self._refresh_display()
            return

        if self._phase == "model":
            models = self._current_models()
            if not models:
                return
            self._selected_model = models[self._cursor]
            self._cursor = 0
            self._phase = "worktree" if self._worktree_options else "name"
            self._refresh_display()
            return

        if self._phase == "worktree":
            if not self._worktree_options:
                self._phase = "name"
                self._refresh_display()
                return
            option = self._worktree_options[self._cursor]
            self._selected_worktree_key = option.key
            if option.key == _NEW_WORKTREE:
                self._phase = "branch"
            else:
                self._phase = "name"
            self._refresh_display()
            return

        if self._phase == "branch":
            self._confirm_branch()
            return

        if self._selected_harness is None:
            return
        self._confirm_name()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input is self._branch_input and self._phase == "branch":
            event.stop()
            self._confirm_branch()
            return
        if event.input is self._name_input and self._phase == "name":
            event.stop()
            self._confirm_name()

    def action_cancel(self) -> None:
        self.post_message(self.Cancelled())

    def _confirm_branch(self) -> None:
        if not self._branch_input.value.strip():
            return
        self._phase = "name"
        self._refresh_display()

    def _confirm_name(self) -> None:
        if self._selected_harness is None:
            return
        worktree_path: str | None = None
        worktree_branch: str | None = None
        key = self._selected_worktree_key
        if key == _NEW_WORKTREE:
            branch = self._branch_input.value.strip()
            if not branch:
                return
            worktree_branch = branch
        elif key not in (None, _MAIN_WORKTREE):
            worktree_path = key
        self.post_message(
            self.Confirmed(
                self._selected_harness,
                self._selected_model or "",
                self._name_input.value,
                worktree_path=worktree_path,
                worktree_branch=worktree_branch,
            )
        )

    def _refresh_display(self) -> None:
        self._display.display = self._phase in {"harness", "model", "worktree"}
        self._branch_input.display = self._phase == "branch"
        self._name_input.display = self._phase == "name"

        if self._phase == "branch":
            self._branch_input.focus()
            return
        if self._phase == "name":
            self._name_input.placeholder = "rogue name (blank = autogenerate)"
            self._name_input.focus()
            return

        self.focus()

        if self._phase == "harness":
            self._display.update(
                self._format_step("Select harness", self._harnesses, _display_harness)
            )
            return

        if self._phase == "model":
            harness = self._selected_harness or ""
            self._display.update(
                self._format_step(
                    f"Select model  (harness: {_display_harness(harness)})",
                    self._current_models(),
                )
            )
            return

        if self._phase == "worktree":
            self._display.update(
                self._format_step(
                    "Select worktree",
                    self._worktree_options,
                    lambda option: option.label,
                )
            )

    def _format_step(
        self,
        header: str,
        options: list[object],
        display_name: Callable[[object], str] = lambda value: str(value),
    ) -> str:
        lines = [escape(header)]
        for idx, option in enumerate(options):
            name = escape(display_name(option))
            if idx == self._cursor:
                lines.append(f"[bold reverse]> {name}[/]")
            else:
                lines.append(f"  {name}")
        return "\n".join(lines)

    def _current_options(self) -> list[object]:
        if self._phase == "harness":
            return self._harnesses
        if self._phase == "model":
            return self._current_models()
        if self._phase == "worktree":
            return self._worktree_options
        return []

    def _current_models(self) -> list[str]:
        if self._selected_harness is None:
            return []
        return _HARNESS_MODELS.get(self._selected_harness, [])

    def _should_select_model(self, harness: str) -> bool:
        if not capabilities_for(harness).model_selection:
            return False
        return bool(_HARNESS_MODELS.get(harness))
