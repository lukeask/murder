"""PaneMirror — _ever_attached semantics: [session vanished] vs (no agent running yet)."""

from __future__ import annotations

import asyncio

from textual.app import App, ComposeResult

from murder.tui.pane_capture import PaneCaptureError
from murder.tui.pane_mirror import PaneMirror


class _App(App):
    def __init__(self, mirror: PaneMirror) -> None:
        super().__init__()
        self._mirror = mirror

    def compose(self) -> ComposeResult:
        yield self._mirror


def _rendered_text(mirror: PaneMirror) -> str:
    return "\n".join(strip.text for strip in mirror.lines)


async def _always_fails(session: str, lines: int) -> str:
    raise PaneCaptureError(f"no such session: {session}")


def test_never_captured_session_shows_no_agent_running() -> None:
    """set_session → failed capture must not show [session vanished]."""
    mirror = PaneMirror(capture_pane=_always_fails)

    async def _run() -> None:
        app = _App(mirror)
        async with app.run_test() as pilot:
            mirror.set_session("murder_demo_planner_plan-foo")
            await mirror.refresh_pane()
            await pilot.pause()
            text = _rendered_text(mirror)
            assert "[session vanished]" not in text, (
                f"Expected '(no agent running yet)' but got: {text!r}"
            )
            assert "no agent running yet" in text

    asyncio.run(_run())


def test_captured_then_lost_session_shows_session_vanished() -> None:
    """set_session → successful capture → failed capture must show [session vanished]."""
    call_count = 0

    async def _capture_then_fail(session: str, lines: int) -> str:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return "Hello from the agent\n"
        raise PaneCaptureError(f"no such session: {session}")

    mirror = PaneMirror(capture_pane=_capture_then_fail)

    async def _run() -> None:
        app = _App(mirror)
        async with app.run_test() as pilot:
            mirror.set_session("murder_demo_planner_plan-foo")
            await mirror.refresh_pane()  # successful — sets _ever_attached
            await pilot.pause()
            await mirror.refresh_pane()  # fails — session died
            await pilot.pause()
            text = _rendered_text(mirror)
            assert "[session vanished]" in text, (
                f"Expected '[session vanished]' after session death, got: {text!r}"
            )

    asyncio.run(_run())


def test_session_change_resets_ever_attached() -> None:
    """Switching sessions resets _ever_attached so old-session vanish doesn't bleed."""
    call_count = 0

    async def _first_ok_rest_fail(session: str, lines: int) -> str:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return "Hello from plan-foo\n"
        raise PaneCaptureError(f"no such session: {session}")

    mirror = PaneMirror(capture_pane=_first_ok_rest_fail)

    async def _run() -> None:
        app = _App(mirror)
        async with app.run_test() as pilot:
            # First session: successful capture → _ever_attached True
            mirror.set_session("murder_demo_planner_plan-foo")
            await mirror.refresh_pane()
            await pilot.pause()

            # Switch to a new session that was never started
            mirror.set_session("murder_demo_planner_plan-bar")
            await mirror.refresh_pane()  # fails — but plan-bar was never captured
            await pilot.pause()

            text = _rendered_text(mirror)
            assert "[session vanished]" not in text, (
                f"Old session's _ever_attached bled into new session: {text!r}"
            )
            assert "no agent running yet" in text

    asyncio.run(_run())
