"""Deprecated shim — capture lives in ``murder.runtime.terminal.capture``."""

from __future__ import annotations

from murder.runtime.terminal.capture import CapturedTerminalFrame, capture_tmux_frame

__all__ = ["CapturedTerminalFrame", "capture_tmux_frame"]
