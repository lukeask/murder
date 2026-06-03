"""Helpers for live-updating RichLog widgets that preserve manual scroll."""

from __future__ import annotations

from collections.abc import Callable

from textual.widgets import RichLog


class LiveRichLog(RichLog):
    """RichLog that only follows the tail when the user was already at the end."""

    _FOLLOW_TAIL_EPSILON = 1

    def __init__(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        kwargs["auto_scroll"] = False
        super().__init__(*args, **kwargs)

    def replace_lines(self, writer: Callable[[], None]) -> None:
        """Rewrite the log while preserving manual scroll unless already at tail."""
        was_following_tail = self.scroll_y >= self.max_scroll_y - self._FOLLOW_TAIL_EPSILON
        scroll_y = self.scroll_y
        self.clear()
        writer()
        if was_following_tail:
            self.scroll_end(animate=False, immediate=False, x_axis=False)
            return
        self.scroll_to(y=min(scroll_y, self.max_scroll_y), animate=False, immediate=True)
