"""Mirrors a tmux pane via periodic capture-pane (fetched over the service bus)."""

from __future__ import annotations

from murder.tui.pane_capture import CapturePaneFn, PaneCaptureError
from murder.tui.live_log import LiveRichLog
from murder.tui.perf_log import PerfLog


class PaneMirror(LiveRichLog):
    """Append-only mirror of a tmux session's active pane."""

    DEFAULT_CSS = """
    PaneMirror {
        border: solid $border;
        height: 1fr;
    }
    """

    def __init__(
        self,
        perf: PerfLog | None = None,
        *,
        capture_pane: CapturePaneFn | None = None,
    ) -> None:
        super().__init__(highlight=False, markup=False, wrap=False)
        self._perf = perf
        self._capture_pane = capture_pane
        self._session: str | None = None
        self._last_text: str = ""
        self._ever_attached = False
        self.border_title = "(no session selected)"

    def set_capture_pane(self, capture_pane: CapturePaneFn) -> None:
        self._capture_pane = capture_pane

    def set_session(self, session: str | None) -> None:
        if session == self._session:
            return
        self._session = session
        self._last_text = ""
        self._ever_attached = False  # reset; only True after a successful capture
        self.clear()
        self.border_title = session or "(no session selected)"
        if session is None:
            self.write("(no agent running yet)")

    async def refresh_pane(self) -> None:
        perf = self._perf
        if perf is not None and perf.enabled:
            with perf.span("tui.pane_mirror.refresh") as dyn:
                prior = self._last_text
                await self._refresh_pane_body()
                dyn["changed"] = self._last_text != prior
            return
        await self._refresh_pane_body()

    async def _refresh_pane_body(self) -> None:
        if not self._session:
            if not self._last_text:
                self.replace_lines(self._write_no_agent_running)
                self._last_text = "(no agent running yet)"
            return
        if self._capture_pane is None:
            return
        try:
            text = await self._capture_pane(self._session, 200)
        except PaneCaptureError:
            vanished = self._ever_attached
            self._session = None
            self._last_text = ""
            self.border_title = "(no session selected)"
            if vanished:
                self.replace_lines(self._write_session_vanished)
            else:
                self.replace_lines(self._write_no_agent_running)
            return
        self._ever_attached = True
        if text == self._last_text:
            return
        self._last_text = text

        def _write() -> None:
            self._write_text(text)

        self.replace_lines(_write)

    def _write_no_agent_running(self) -> None:
        self.write("(no agent running yet)")

    def _write_session_vanished(self) -> None:
        self.write("[session vanished]")

    def _write_text(self, text: str) -> None:
        for line in text.splitlines():
            self.write(line)
