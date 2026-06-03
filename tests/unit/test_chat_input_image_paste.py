"""Tests for ChatInput ctrl+v image paste (Part C of plan-image-paste)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from textual.app import App, ComposeResult
from textual.widgets import Label

import murder.tui.clipboard_image as clip_mod
from murder.tui.chat_input import ChatInput


class _ChatApp(App[None]):
    def __init__(self) -> None:
        super().__init__()
        self.chat: ChatInput | None = None
        self.received_messages: list[str] = []

    def compose(self) -> ComposeResult:
        yield Label("")

    def on_mount(self) -> None:
        chat = ChatInput()
        self.chat = chat
        self.mount(chat)
        self.set_focus(chat)

    def on_chat_input_user_message(self, event: ChatInput.UserMessage) -> None:
        self.received_messages.append(event.text)


def test_ctrl_v_no_image_does_not_insert_token(monkeypatch) -> None:
    monkeypatch.setattr(clip_mod, "has_clipboard_image", AsyncMock(return_value=False))
    monkeypatch.setattr(clip_mod, "read_clipboard_image_png", AsyncMock(return_value=None))

    async def _run() -> str:
        app = _ChatApp()
        async with app.run_test() as pilot:
            assert app.chat is not None
            await pilot.press("ctrl+v")
            await pilot.pause()
            await pilot.pause()
            return app.chat.text

    text = asyncio.run(_run())
    assert "[Image #" not in text
    assert "pasting" not in text


def test_ctrl_v_with_image_writes_tmp_file_and_inserts_token(
    tmp_path: Path, monkeypatch
) -> None:
    png_bytes = b"\x89PNG\r\n\x1a\nfakedata"
    monkeypatch.setattr(clip_mod, "has_clipboard_image", AsyncMock(return_value=True))
    monkeypatch.setattr(
        clip_mod, "read_clipboard_image_png", AsyncMock(return_value=png_bytes)
    )

    written_paths: list[Path] = []

    async def _run() -> tuple[str, dict]:
        app = _ChatApp()
        async with app.run_test() as pilot:
            assert app.chat is not None
            await pilot.press("ctrl+v")
            await pilot.pause()
            await pilot.pause()
            text = app.chat.text
            pending = dict(app.chat._pending_image_paths)
            written_paths.extend(pending.values())
            return text, pending

    text, pending = asyncio.run(_run())

    # Token stays visible in the widget
    assert "[Image #1]" in text
    # Path map has one entry
    assert len(pending) == 1
    # Temp file was created with correct content
    assert len(written_paths) == 1
    p = written_paths[0]
    assert p.exists()
    assert p.read_bytes() == png_bytes
    assert p.name.startswith("murder-clipboard-")
    assert p.suffix == ".png"


def test_ctrl_v_read_failure_replaces_token_with_failed(monkeypatch) -> None:
    monkeypatch.setattr(clip_mod, "has_clipboard_image", AsyncMock(return_value=True))
    monkeypatch.setattr(
        clip_mod, "read_clipboard_image_png", AsyncMock(return_value=None)
    )

    async def _run() -> str:
        app = _ChatApp()
        async with app.run_test() as pilot:
            assert app.chat is not None
            await pilot.press("ctrl+v")
            await pilot.pause()
            await pilot.pause()
            return app.chat.text

    text = asyncio.run(_run())
    assert "[Image paste failed]" in text
    assert "[Image #" not in text


def test_send_substitutes_token_with_absolute_path(monkeypatch) -> None:
    png_bytes = b"\x89PNG\r\n\x1a\n"
    monkeypatch.setattr(clip_mod, "has_clipboard_image", AsyncMock(return_value=True))
    monkeypatch.setattr(
        clip_mod, "read_clipboard_image_png", AsyncMock(return_value=png_bytes)
    )

    async def _run() -> list[str]:
        app = _ChatApp()
        async with app.run_test() as pilot:
            assert app.chat is not None
            await pilot.press("ctrl+v")
            await pilot.pause()
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            return list(app.received_messages)

    messages = asyncio.run(_run())
    assert len(messages) == 1
    msg = messages[0]
    # Token replaced by absolute path, ending in .png
    assert "[Image #" not in msg
    assert msg.endswith(".png")
    # The path actually exists on disk
    assert Path(msg).exists()


def test_ctrl_d_cleans_up_temp_file(monkeypatch) -> None:
    png_bytes = b"\x89PNG\r\n\x1a\n"
    monkeypatch.setattr(clip_mod, "has_clipboard_image", AsyncMock(return_value=True))
    monkeypatch.setattr(
        clip_mod, "read_clipboard_image_png", AsyncMock(return_value=png_bytes)
    )

    saved_paths: list[Path] = []

    async def _run() -> None:
        app = _ChatApp()
        async with app.run_test() as pilot:
            assert app.chat is not None
            await pilot.press("ctrl+v")
            await pilot.pause()
            await pilot.pause()
            saved_paths.extend(app.chat._pending_image_paths.values())
            # ctrl+d clears and cleans up
            await pilot.press("ctrl+d")
            await pilot.pause()

    asyncio.run(_run())

    assert len(saved_paths) == 1
    # File should have been deleted
    assert not saved_paths[0].exists()
