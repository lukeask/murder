"""Tests for note_capture ctrl+v image paste (t033).

After C14/V2 the screen no longer writes ``.murder/`` directly — it calls an
injected async ``upload_image`` callable (service-backed in production) and
inserts the returned path. These tests drive a fake uploader.
"""

from __future__ import annotations

import asyncio
import secrets
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock

from textual.app import App, ComposeResult
from textual.widgets import Label

import murder.app.tui.clipboard_image as clip_mod
from murder.app.tui.note_capture import NoteCaptureScreen


def _fake_uploader(images_dir: Path):
    """Stand-in for the service image.upload RPC: store + return the path."""

    async def _upload(data: bytes) -> Path:
        images_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d%H%M%S")
        fpath = images_dir / f"note-img-{ts}-{secrets.token_hex(2)}.png"
        fpath.write_bytes(data)
        return fpath

    return _upload


class _NoteApp(App[None]):
    def __init__(self, images_dir: Path, *, upload=None) -> None:
        super().__init__()
        self._images_dir = images_dir
        self._upload = upload or _fake_uploader(images_dir)
        self.screen_ref: NoteCaptureScreen | None = None

    def compose(self) -> ComposeResult:
        yield Label("")

    def on_mount(self) -> None:
        screen = NoteCaptureScreen(
            initial_draft="",
            load_recent_rows=lambda: [],
            upload_image=self._upload,
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


def test_ctrl_v_with_image_uploads_and_inserts_markdown(
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


def test_ctrl_v_upload_failure_inserts_failed_placeholder(
    tmp_path: Path, monkeypatch
) -> None:
    png_bytes = b"\x89PNG\r\n\x1a\n"
    monkeypatch.setattr(clip_mod, "has_clipboard_image", AsyncMock(return_value=True))
    monkeypatch.setattr(
        clip_mod, "read_clipboard_image_png", AsyncMock(return_value=png_bytes)
    )

    async def _failing_upload(_data: bytes) -> Path:
        raise RuntimeError("service unavailable")

    async def _run() -> str:
        app = _NoteApp(images_dir=tmp_path / "images", upload=_failing_upload)
        async with app.run_test() as pilot:
            screen = app.screen_ref
            assert screen is not None
            screen.set_focus(screen._draft_widget)
            await pilot.press("ctrl+v")
            await pilot.pause()
            await pilot.pause()
            return screen._draft_widget.text

    text = asyncio.run(_run())
    assert "[Image upload failed]" in text
