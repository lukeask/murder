"""Tests for note_capture ctrl+v image paste (t033)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from textual.app import App, ComposeResult
from textual.widgets import Label

import murder.app.tui.clipboard_image as clip_mod
from murder.app.tui.note_capture import NoteCaptureScreen


class _NoteApp(App[None]):
    def __init__(self, images_dir: Path) -> None:
        super().__init__()
        self._images_dir = images_dir
        self.screen_ref: NoteCaptureScreen | None = None

    def compose(self) -> ComposeResult:
        yield Label("")

    def on_mount(self) -> None:
        screen = NoteCaptureScreen(
            initial_draft="",
            load_recent_rows=lambda: [],
            images_dir=self._images_dir,
        )
        self.screen_ref = screen
        self.push_screen(screen)


def test_ctrl_v_no_image_does_not_insert_placeholder(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(clip_mod, "has_clipboard_image", AsyncMock(return_value=False))
    monkeypatch.setattr(clip_mod, "read_clipboard_image_png", AsyncMock(return_value=None))

    images_dir = tmp_path / "images"

    async def _run() -> str:
        app = _NoteApp(images_dir=images_dir)
        async with app.run_test() as pilot:
            screen = app.screen_ref
            assert screen is not None
            screen.set_focus(screen._draft_widget)
            await pilot.press("ctrl+v")
            await pilot.pause()
            await pilot.pause()
            return screen._draft_widget.text

    text = asyncio.run(_run())
    assert "[Image #" not in text
    assert "pasting" not in text


def test_ctrl_v_with_image_writes_file_and_inserts_markdown(
    tmp_path: Path, monkeypatch
) -> None:
    png_bytes = b"\x89PNG\r\n\x1a\nfakedata"
    monkeypatch.setattr(clip_mod, "has_clipboard_image", AsyncMock(return_value=True))
    monkeypatch.setattr(
        clip_mod, "read_clipboard_image_png", AsyncMock(return_value=png_bytes)
    )

    images_dir = tmp_path / "images"

    async def _run() -> str:
        app = _NoteApp(images_dir=images_dir)
        async with app.run_test() as pilot:
            screen = app.screen_ref
            assert screen is not None
            screen.set_focus(screen._draft_widget)
            await pilot.press("ctrl+v")
            await pilot.pause()
            await pilot.pause()
            return screen._draft_widget.text

    text = asyncio.run(_run())

    assert "![image](" in text
    assert "[Image #" not in text

    written = list(images_dir.glob("note-img-*.png"))
    assert len(written) == 1
    assert written[0].read_bytes() == png_bytes


def test_ctrl_v_read_failure_inserts_failed_placeholder(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(clip_mod, "has_clipboard_image", AsyncMock(return_value=True))
    monkeypatch.setattr(
        clip_mod, "read_clipboard_image_png", AsyncMock(return_value=None)
    )

    images_dir = tmp_path / "images"

    async def _run() -> str:
        app = _NoteApp(images_dir=images_dir)
        async with app.run_test() as pilot:
            screen = app.screen_ref
            assert screen is not None
            screen.set_focus(screen._draft_widget)
            await pilot.press("ctrl+v")
            await pilot.pause()
            await pilot.pause()
            return screen._draft_widget.text

    text = asyncio.run(_run())
    assert "[Image paste failed]" in text
    assert "[Image #" not in text


def test_image_written_to_images_dir_with_correct_pattern(
    tmp_path: Path, monkeypatch
) -> None:
    png_bytes = b"\x89PNG\r\n\x1a\n"
    monkeypatch.setattr(clip_mod, "has_clipboard_image", AsyncMock(return_value=True))
    monkeypatch.setattr(
        clip_mod, "read_clipboard_image_png", AsyncMock(return_value=png_bytes)
    )

    images_dir = tmp_path / ".murder" / "images"

    async def _run() -> None:
        app = _NoteApp(images_dir=images_dir)
        async with app.run_test() as pilot:
            screen = app.screen_ref
            assert screen is not None
            screen.set_focus(screen._draft_widget)
            await pilot.press("ctrl+v")
            await pilot.pause()
            await pilot.pause()

    asyncio.run(_run())

    written = list(images_dir.glob("note-img-*.png"))
    assert len(written) == 1
    name = written[0].name
    # note-img-<14-digit-ts>-<4hex>.png
    import re
    assert re.match(r"note-img-\d{14}-[0-9a-f]{4}\.png", name), name
