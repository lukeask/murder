from __future__ import annotations

from collections.abc import Iterable


class PaneSimulator:
    """Small builder for terminal-pane style fixtures used by harness tests."""

    def __init__(self, lines: Iterable[str] = ()) -> None:
        self._lines = list(lines)

    def add(self, *lines: str) -> "PaneSimulator":
        self._lines.extend(lines)
        return self

    def render(self) -> str:
        return "\n".join(self._lines)
