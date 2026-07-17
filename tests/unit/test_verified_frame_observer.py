from __future__ import annotations

import asyncio

from murder.llm.harness_control.runtime.tmux_frame_observer import TmuxFrameObserver
from murder.runtime.terminal import tmux

FRAME_LINES = 300


def test_frame_observer_retains_ansi_and_monotonic_capture_provenance(monkeypatch) -> None:
    async def dimensions(session: str) -> tuple[int, int]:
        assert session == "harness-1"
        return (220, 50)

    async def capture(session: str, *, lines: int, escapes: bool) -> str:
        assert session == "harness-1"
        assert lines == FRAME_LINES
        assert escapes is True
        return "\x1b[1mcomposer\x1b[0m"

    async def viewport(session: str, *, escapes: bool) -> str:
        assert session == "harness-1"
        assert escapes is True
        return "\x1b[1mcomposer\x1b[0m"

    monkeypatch.setattr(tmux, "pane_dimensions", dimensions)
    monkeypatch.setattr(tmux, "capture_pane", capture)
    monkeypatch.setattr(tmux, "capture_viewport", viewport)

    async def scenario() -> None:
        observer = TmuxFrameObserver("harness-1", "codex", pane_epoch=4)
        first = await observer.capture_frame()
        second = await observer.capture_frame()
        assert (first.width, first.height, first.ansi_preserved) == (220, 50, True)
        assert first.raw_text.startswith("\x1b[")
        assert first.viewport_text == first.raw_text
        assert (first.pane_epoch, first.capture_sequence) == (4, 1)
        assert (second.pane_epoch, second.capture_sequence) == (4, 2)
        assert first.frame_id != second.frame_id

    asyncio.run(scenario())


def test_pane_dimensions_rejects_invalid_tmux_response(monkeypatch) -> None:
    async def invalid(*args: str, **kwargs: object) -> tuple[int, str, str]:
        return (0, "bad", "")

    monkeypatch.setattr(tmux, "_tmux", invalid)

    async def scenario() -> None:
        try:
            await tmux.pane_dimensions("harness-1")
        except tmux.TmuxError as exc:
            assert "invalid pane dimensions" in str(exc)
        else:
            raise AssertionError("invalid dimensions must not become frame provenance")

    asyncio.run(scenario())
