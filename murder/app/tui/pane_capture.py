"""TUI boundary type for pane text fetched via the service bus."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

CapturePaneFn = Callable[[str, int], Awaitable[str]]

class PaneCaptureError(Exception):
    """Pane capture failed or the session is gone."""
