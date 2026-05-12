"""Mirrors a tmux pane via periodic capture-pane."""

from __future__ import annotations

from textual.widgets import RichLog

from murder import tmux


class PaneMirror(RichLog):
    """Append-only mirror of a tmux session's active pane."""

    DEFAULT_CSS = """
    PaneMirror {
        border: solid $border;
        height: 1fr;
    }
    """

    def __init__(self) -> None:
        super().__init__(highlight=False, markup=False, wrap=False, auto_scroll=True)
        self._session: str | None = None
        self._last_text: str = ""
        self._ever_attached = False
        self.border_title = "(no session selected)"

    def set_session(self, session: str | None) -> None:
        if session == self._session:
            return
        self._session = session
        self._last_text = ""
        self.clear()
        self.border_title = session or "(no session selected)"
        if session is None:
            self.write("(no agent running yet)")
        else:
            self._ever_attached = True

    async def refresh_pane(self) -> None:
        if not self._session:
            if not self._last_text:
                self.clear()
                self.write("(no agent running yet)")
                self._last_text = "(no agent running yet)"
            return
        try:
            text = await tmux.capture_pane(self._session, lines=200)
        except tmux.TmuxError:
            vanished = self._ever_attached
            self._session = None
            self._last_text = ""
            self.clear()
            self.border_title = "(no session selected)"
            self.write("[session vanished]" if vanished else "(no agent running yet)")
            return
        if text == self._last_text:
            return
        self._last_text = text
        self.clear()
        for line in text.splitlines():
            self.write(line)
