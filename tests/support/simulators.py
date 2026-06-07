from __future__ import annotations

from collections.abc import Iterable
from typing import Any


class FakeAsyncLoader:
    """Awaitable callable stub for store loaders (e.g. UsageDrillInLoader).

    Records the kwargs of each await for assertions and returns a configurable
    value. Replaces ad-hoc ``AsyncMock()`` instances where the loader is passed
    to a store constructor but rarely (or never) awaited.
    """

    def __init__(self, return_value: Any = None) -> None:
        self.return_value = return_value
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self.return_value


class PaneSimulator:
    """Small builder for terminal-pane style fixtures used by harness tests."""

    def __init__(self, lines: Iterable[str] = ()) -> None:
        self._lines = list(lines)

    def add(self, *lines: str) -> "PaneSimulator":
        self._lines.extend(lines)
        return self

    def render(self) -> str:
        return "\n".join(self._lines)
