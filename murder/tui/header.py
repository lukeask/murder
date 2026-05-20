"""Header bar: project name + ticket status counts."""

from __future__ import annotations

from textual.widgets import Static

from murder.service.client_api import DispatchSnapshot

_STATUSES = (
    "draft",
    "planned",
    "ready",
    "in_progress",
    "blocked",
    "done",
    "failed",
    "archived",
)


class Header(Static):
    """Project name on the left, live status counts on the right."""

    DEFAULT_CSS = """
    Header {
        height: 1;
        background: $boost;
        color: $text;
        padding: 0 1;
    }
    """

    def __init__(self, project: str) -> None:
        super().__init__("murder")
        self.project = project
        self._counts: dict[str, int] = {s: 0 for s in _STATUSES}
        self._view = "planning"

    def refresh_from_snapshot(self, snapshot: DispatchSnapshot) -> None:
        counts = {s: 0 for s in _STATUSES}
        for ticket in snapshot.tickets:
            key = ticket.status.value
            if key in counts:
                counts[key] += 1
        self._counts = counts
        self._update_text()

    def set_view(self, view: str) -> None:
        self._view = view
        self._update_text()

    def _update_text(self) -> None:
        parts = " ".join(f"{s}:{self._counts[s]}" for s in _STATUSES)
        project = "[red][unconfigured][/red]" if self.project == "TODO_SET_ME" else self.project
        planning_label = "[1 planning]"
        tabs = " ".join(
            f"[b]{label}[/b]" if key == self._view else label
            for key, label in (
                ("planning", planning_label),
                ("crows", "[2 crows]"),
                ("schedule", "[3 dispatch]"),
            )
        )
        self.update(f"[b]murder[/b] · {project} · {tabs} · {parts}")
