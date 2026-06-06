"""Raw-key mode key mapping for chat input."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from textual.app import App, ComposeResult

from murder.app.tui.chat_input import ChatInput
from murder.app.tui.chat_input import _harness_delivery


def _key(*, key: str, character: str | None = None, is_printable: bool = False) -> SimpleNamespace:
    return SimpleNamespace(key=key, character=character, is_printable=is_printable)


def test_printable_character_is_literal() -> None:
    assert _harness_delivery(_key(key="a", character="a", is_printable=True)) == ("a", True)


def test_named_special_keys() -> None:
    assert _harness_delivery(_key(key="up")) == ("Up", False)
    assert _harness_delivery(_key(key="enter")) == ("Enter", False)
    # space has a printable character but must use the named key so the
    # notification shows "Space" rather than an invisible trailing space
    assert _harness_delivery(_key(key="space", character=" ", is_printable=True)) == ("Space", False)


def test_ctrl_combo() -> None:
    assert _harness_delivery(_key(key="ctrl+c")) == ("C-c", False)


class _ChatInputApp(App[None]):
    def __init__(self) -> None:
        super().__init__()
        self.chat = ChatInput()
        self.events: list[str] = []

    def compose(self) -> ComposeResult:
        yield self.chat

    def on_mount(self) -> None:
        self.chat.focus()

    def on_chat_input_murder_confirm(self, event: ChatInput.MurderConfirm) -> None:
        del event
        self.events.append("confirm")

    def on_chat_input_murder_cancel(self, event: ChatInput.MurderCancel) -> None:
        del event
        self.events.append("cancel")


def test_murder_confirm_mode_accepts_m() -> None:
    app = _ChatInputApp()

    async def _run() -> None:
        async with app.run_test() as pilot:
            app.chat.set_murder_confirm("crow-t001")
            await pilot.pause()
            await pilot.press("m")
            await pilot.pause()

            assert app.events == ["confirm"]

    asyncio.run(_run())


def test_murder_confirm_mode_accepts_ctrl_m() -> None:
    app = _ChatInputApp()

    async def _run() -> None:
        async with app.run_test() as pilot:
            app.chat.set_murder_confirm("crow-t001")
            await pilot.pause()
            await pilot.press("ctrl+m")
            await pilot.pause()

            assert app.events == ["confirm"]

    asyncio.run(_run())


def test_murder_confirm_mode_cancels_on_other_key() -> None:
    app = _ChatInputApp()

    async def _run() -> None:
        async with app.run_test() as pilot:
            app.chat.set_murder_confirm("crow-t001")
            await pilot.pause()
            await pilot.press("x")
            await pilot.pause()

            assert app.events == ["cancel"]

    asyncio.run(_run())
