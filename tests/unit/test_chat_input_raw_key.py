"""Raw-key mode key mapping for chat input."""

from __future__ import annotations

from types import SimpleNamespace

from murder.tui.chat_input import _harness_delivery


def _key(*, key: str, character: str | None = None, is_printable: bool = False) -> SimpleNamespace:
    return SimpleNamespace(key=key, character=character, is_printable=is_printable)


def test_printable_character_is_literal() -> None:
    assert _harness_delivery(_key(key="a", character="a", is_printable=True)) == ("a", True)


def test_named_special_keys() -> None:
    assert _harness_delivery(_key(key="up")) == ("Up", False)
    assert _harness_delivery(_key(key="enter")) == ("Enter", False)


def test_ctrl_combo() -> None:
    assert _harness_delivery(_key(key="ctrl+c")) == ("C-c", False)
