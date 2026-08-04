"""Transport-level tmux viewport capture (no DB access)."""

from __future__ import annotations

from dataclasses import dataclass

from murder.runtime.terminal import tmux


@dataclass(frozen=True)
class CapturedTerminalFrame:
    data: str
    columns: int
    rows: int


async def capture_tmux_frame(tmux_name: str) -> CapturedTerminalFrame:
    """Capture a tmux viewport by its transport session name."""

    data = await tmux.capture_viewport(tmux_name, escapes=True)
    columns, rows = await tmux.pane_dimensions(tmux_name)
    return CapturedTerminalFrame(data=data, columns=columns, rows=rows)


__all__ = ["CapturedTerminalFrame", "capture_tmux_frame"]
