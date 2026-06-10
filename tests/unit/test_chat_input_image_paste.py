"""Tests for ChatInput ctrl+v image paste (Part C of plan-image-paste).

COOKBOOK = the paste→token→send-substitution→cleanup contract.
EDGE CASES = no-image passthrough and clipboard-read failure.

The token-substitution / temp-file / cleanup logic lives inside the live
TextArea (`self.text`, `self.insert`, `self._pending_image_paths`), so these
exercise a real Textual widget rather than a pure-data unit.  The AsyncMock on
clip_mod is an I/O-boundary patch (the clipboard subprocess), not the unit
under test.

TODO(support): a `drive_chat_input` helper belongs in simulators.py once the
pilot-driver pattern is shared across chat-input tests.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TypeVar
from unittest.mock import AsyncMock

from textual.app import App, ComposeResult
from textual.widgets import Label

import murder.app.tui.clipboard_image as clip_mod
from murder.app.tui.chat_input import ChatInput

_T = TypeVar("_T")


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


def _patch_clipboard(monkeypatch, *, has_image: bool, png: bytes | None) -> None:
    monkeypatch.setattr(clip_mod, "has_clipboard_image", AsyncMock(return_value=has_image))
    monkeypatch.setattr(clip_mod, "read_clipboard_image_png", AsyncMock(return_value=png))


def _drive(body: Callable[[_ChatApp, object], Awaitable[_T]]) -> _T:
    """Boot a headless ChatApp, run `body(app, pilot)`, return its result."""

    async def _run() -> _T:
        app = _ChatApp()
        async with app.run_test() as pilot:
            assert app.chat is not None
            return await body(app, pilot)

    return asyncio.run(_run())


async def _paste(app: _ChatApp, pilot) -> None:
    await pilot.press("ctrl+v")
    await pilot.pause()
    await pilot.pause()


# ============================================================
# === COOKBOOK ===============================================
# ============================================================


def test_ctrl_v_with_image_writes_tmp_file_and_inserts_token(monkeypatch) -> None:
    png_bytes = b"\x89PNG\r\n\x1a\nfakedata"
    _patch_clipboard(monkeypatch, has_image=True, png=png_bytes)

    async def _body(app, pilot):
        await _paste(app, pilot)
        return app.chat.text, dict(app.chat._pending_image_paths)

    text, pending = _drive(_body)

    # Token stays visible in the widget; one temp file mapped to it.
    assert "[Image #1]" in text
    assert len(pending) == 1
    (p,) = pending.values()
    assert p.exists()
    assert p.read_bytes() == png_bytes
    assert p.name.startswith("murder-clipboard-")
    assert p.suffix == ".png"


def test_send_substitutes_token_with_absolute_path(monkeypatch) -> None:
    _patch_clipboard(monkeypatch, has_image=True, png=b"\x89PNG\r\n\x1a\n")

    async def _body(app, pilot):
        await _paste(app, pilot)
        await pilot.press("enter")
        await pilot.pause()
        return list(app.received_messages)

    messages = _drive(_body)
    assert len(messages) == 1
    msg = messages[0]
    # Token replaced by an absolute path that exists on disk.
    assert "[Image #" not in msg
    assert msg.endswith(".png")
    assert Path(msg).exists()


def test_ctrl_d_cleans_up_temp_file(monkeypatch) -> None:
    _patch_clipboard(monkeypatch, has_image=True, png=b"\x89PNG\r\n\x1a\n")

    async def _body(app, pilot):
        await _paste(app, pilot)
        saved = list(app.chat._pending_image_paths.values())
        await pilot.press("ctrl+d")
        await pilot.pause()
        return saved

    saved = _drive(_body)
    assert len(saved) == 1
    assert not saved[0].exists()


# ============================================================
# === EDGE CASES =============================================
# ============================================================


def test_ctrl_v_no_image_does_not_insert_token(monkeypatch) -> None:
    _patch_clipboard(monkeypatch, has_image=False, png=None)

    async def _body(app, pilot):
        await _paste(app, pilot)
        return app.chat.text

    text = _drive(_body)
    assert "[Image #" not in text
    assert "pasting" not in text


def test_ctrl_v_read_failure_replaces_token_with_failed(monkeypatch) -> None:
    _patch_clipboard(monkeypatch, has_image=True, png=None)

    async def _body(app, pilot):
        await _paste(app, pilot)
        return app.chat.text

    text = _drive(_body)
    assert "[Image paste failed]" in text
    assert "[Image #" not in text
