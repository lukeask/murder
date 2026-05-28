"""Fake ``murder.terminal.tmux`` module for harness unit tests.

Usage in a test::

    @pytest.fixture
    def fake_tmux(monkeypatch):
        import murder.terminal.tmux as tmux_mod
        ft = FakeTmux()
        ft.install(monkeypatch, tmux_mod)
        return ft

``capture_pane`` pops queued texts in order; the last item is repeated when
the queue is exhausted, so a single ``queue_pane(idle_text)`` works for any
number of polls.  ``queue_error`` injects a ``TmuxError``.
"""

from __future__ import annotations


class FakeTmux:
    class TmuxError(Exception):
        pass

    LARGE_PAYLOAD_BYTES = 1024

    def __init__(self) -> None:
        self._pane_queue: list[str | Exception] = []
        self.calls: list[tuple[str, tuple, dict]] = []

    # ── queue helpers ─────────────────────────────────────────────────────────

    def queue_pane(self, text: str) -> None:
        self._pane_queue.append(text)

    def queue_error(self, msg: str = "session gone") -> None:
        self._pane_queue.append(self.TmuxError(msg))

    def _next_pane(self) -> str:
        if not self._pane_queue:
            return ""
        # Repeat last item when only one remains (avoids IndexError on long polls)
        item = self._pane_queue[0] if len(self._pane_queue) == 1 else self._pane_queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    # ── fake async ops ────────────────────────────────────────────────────────

    async def capture_pane(self, session: str, *, lines: int = 120) -> str:
        self.calls.append(("capture_pane", (session,), {"lines": lines}))
        return self._next_pane()

    async def create_session(self, name: str, cwd: object, cmd: list[str]) -> None:
        self.calls.append(("create_session", (name, cwd, cmd), {}))

    async def send_keys(
        self, session: str, text: str, *, literal: bool = True, enter: bool = True
    ) -> None:
        self.calls.append(("send_keys", (session, text), {"literal": literal, "enter": enter}))

    async def interrupt(self, session: str) -> None:
        self.calls.append(("interrupt", (session,), {}))

    async def paste_buffer_literal(self, session: str, text: str) -> None:
        self.calls.append(("paste_buffer_literal", (session, text), {}))

    # ── queue / call helpers ──────────────────────────────────────────────────

    def reset_queue(self) -> None:
        """Discard any queued pane texts (use between test phases)."""
        self._pane_queue.clear()

    def call_names(self) -> list[str]:
        return [c[0] for c in self.calls]

    def calls_to(self, fn: str) -> list[tuple[tuple, dict]]:
        return [(args, kw) for name, args, kw in self.calls if name == fn]

    # ── monkeypatch installer ─────────────────────────────────────────────────

    def install(self, monkeypatch: object, tmux_mod: object) -> None:
        mp = monkeypatch  # type: ignore[assignment]
        mp.setattr(tmux_mod, "TmuxError", self.TmuxError)
        mp.setattr(tmux_mod, "LARGE_PAYLOAD_BYTES", self.LARGE_PAYLOAD_BYTES)
        mp.setattr(tmux_mod, "capture_pane", self.capture_pane)
        mp.setattr(tmux_mod, "create_session", self.create_session)
        mp.setattr(tmux_mod, "send_keys", self.send_keys)
        mp.setattr(tmux_mod, "interrupt", self.interrupt)
        mp.setattr(tmux_mod, "paste_buffer_literal", self.paste_buffer_literal)
