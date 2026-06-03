from __future__ import annotations

import asyncio

from textual.app import App, ComposeResult

from murder.tui.pane_mirror import PaneMirror
from murder.tui.planning_mode_widgets import ChatLog


class _ChatApp(App[None]):
    CSS = """
    ChatLog {
        height: 8;
    }
    """

    def __init__(self, log: ChatLog) -> None:
        super().__init__()
        self._log = log

    def compose(self) -> ComposeResult:
        yield self._log


class _MirrorApp(App[None]):
    CSS = """
    PaneMirror {
        height: 8;
    }
    """

    def __init__(self, mirror: PaneMirror) -> None:
        super().__init__()
        self._mirror = mirror

    def compose(self) -> ComposeResult:
        yield self._mirror


def _turns(count: int) -> list[tuple[str, str]]:
    return [("assistant", f"line {idx}") for idx in range(count)]


def _pane_text(count: int) -> str:
    return "\n".join(f"line {idx}" for idx in range(count))


def test_chat_log_preserves_manual_scroll_on_refresh() -> None:
    log = ChatLog(agent_label="collaborator")

    async def _run() -> None:
        app = _ChatApp(log)
        async with app.run_test() as pilot:
            log.set_turns(_turns(20))
            await pilot.pause()
            assert log.max_scroll_y > 0

            preserved_y = max(0, log.max_scroll_y - 4)
            log.scroll_to(y=preserved_y, animate=False, immediate=True)
            await pilot.pause()

            log.set_turns(_turns(24))
            await pilot.pause()
            assert log.scroll_y == preserved_y
            assert log.scroll_y < log.max_scroll_y

    asyncio.run(_run())


def test_chat_log_follows_tail_when_already_at_bottom() -> None:
    log = ChatLog(agent_label="collaborator")

    async def _run() -> None:
        app = _ChatApp(log)
        async with app.run_test() as pilot:
            log.set_turns(_turns(20))
            await pilot.pause()
            log.scroll_end(animate=False, immediate=True, x_axis=False)
            await pilot.pause()

            log.set_turns(_turns(24))
            await pilot.pause()
            assert log.scroll_y == log.max_scroll_y

    asyncio.run(_run())


def test_pane_mirror_preserves_manual_scroll_on_refresh() -> None:
    calls = 0

    async def _capture(_session: str, _lines: int) -> str:
        nonlocal calls
        calls += 1
        return _pane_text(20 if calls == 1 else 24)

    mirror = PaneMirror(capture_pane=_capture)

    async def _run() -> None:
        app = _MirrorApp(mirror)
        async with app.run_test() as pilot:
            mirror.set_session("murder_demo_planner_plan-foo")
            await mirror.refresh_pane()
            await pilot.pause()
            assert mirror.max_scroll_y > 0

            preserved_y = max(0, mirror.max_scroll_y - 4)
            mirror.scroll_to(y=preserved_y, animate=False, immediate=True)
            await pilot.pause()

            await mirror.refresh_pane()
            await pilot.pause()
            assert mirror.scroll_y == preserved_y
            assert mirror.scroll_y < mirror.max_scroll_y

    asyncio.run(_run())
