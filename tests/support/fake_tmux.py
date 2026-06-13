"""Fake ``murder.runtime.terminal.tmux`` module for harness unit tests.

Usage in a test::

    @pytest.fixture
    def fake_tmux(monkeypatch):
        import murder.runtime.terminal.tmux as tmux_mod
        ft = FakeTmux()
        ft.install(monkeypatch, tmux_mod)
        return ft

``capture_pane`` pops queued texts in order; the last item is repeated when
the queue is exhausted, so a single ``queue_pane(idle_text)`` works for any
number of polls.  ``queue_error`` injects a ``TmuxError``.

Session ops (``session_exists``/``kill_session``/``rename_session``/
``list_sessions``) are stateful: seed live sessions with ``add_session(name)``
or flip the default for unknown names with ``set_session_exists(True)``;
``create_session`` registers the session automatically. Kills/renames are
recorded on ``killed_sessions``/``renamed_sessions`` for assertions.
"""

from __future__ import annotations


class FakeTmux:
    class TmuxError(Exception):
        pass

    LARGE_PAYLOAD_BYTES = 1024

    def __init__(self) -> None:
        self._pane_queue: list[str | Exception] = []
        self.calls: list[tuple[str, tuple, dict]] = []
        # Settable session state: tests flip `session_exists_returns` (a default
        # for unknown names) and/or seed `_sessions` to model live sessions.
        self.session_exists_returns: bool = False
        self._sessions: set[str] = set()
        self.killed_sessions: list[str] = []
        self.renamed_sessions: list[tuple[str, str]] = []

    # ── queue helpers ─────────────────────────────────────────────────────────

    def queue_pane(self, text: str) -> None:
        self._pane_queue.append(text)

    def queue_error(self, msg: str = "session gone") -> None:
        self._pane_queue.append(self.TmuxError(msg))

    # ── session-state helpers ─────────────────────────────────────────────────

    def add_session(self, name: str) -> None:
        """Mark `name` as a live session (so `session_exists` returns True)."""
        self._sessions.add(name)

    def set_session_exists(self, exists: bool) -> None:
        """Set the default `session_exists` result for un-tracked names."""
        self.session_exists_returns = exists

    def _next_pane(self) -> str:
        if not self._pane_queue:
            return ""
        # Repeat last item when only one remains (avoids IndexError on long polls)
        item = self._pane_queue[0] if len(self._pane_queue) == 1 else self._pane_queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    # ── fake async ops ────────────────────────────────────────────────────────

    async def capture_pane(
        self, name: str, lines: int = 200, *, perf: object | None = None, escapes: bool = False
    ) -> str:
        self.calls.append(("capture_pane", (name,), {"lines": lines, "escapes": escapes}))
        return self._next_pane()

    async def create_session(self, name: str, cwd: object, cmd: list[str]) -> None:
        self.calls.append(("create_session", (name, cwd, cmd), {}))
        self._sessions.add(name)

    async def session_exists(self, name: str) -> bool:
        self.calls.append(("session_exists", (name,), {}))
        if name in self._sessions:
            return True
        return self.session_exists_returns

    async def kill_session(self, name: str) -> None:
        self.calls.append(("kill_session", (name,), {}))
        self.killed_sessions.append(name)
        self._sessions.discard(name)

    async def rename_session(self, old_name: str, new_name: str) -> bool:
        self.calls.append(("rename_session", (old_name, new_name), {}))
        if old_name == new_name:
            return False
        exists = old_name in self._sessions or self.session_exists_returns
        if not exists:
            return False
        self.renamed_sessions.append((old_name, new_name))
        if old_name in self._sessions:
            self._sessions.discard(old_name)
            self._sessions.add(new_name)
        return True

    async def list_sessions(self, prefix: str | None = None) -> list[str]:
        self.calls.append(("list_sessions", (prefix,), {}))
        return [n for n in sorted(self._sessions) if prefix is None or n.startswith(prefix)]

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
        mp.setattr(tmux_mod, "session_exists", self.session_exists)
        mp.setattr(tmux_mod, "kill_session", self.kill_session)
        mp.setattr(tmux_mod, "rename_session", self.rename_session)
        mp.setattr(tmux_mod, "list_sessions", self.list_sessions)
