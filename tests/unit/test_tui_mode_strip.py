"""Unit tests for dispatch mode picker behavior."""

from __future__ import annotations

from murder.tui.dispatch.mode_strip import ModeStrip


class _FakeModeStrip(ModeStrip):
    """Simple stand-in that avoids requiring a mounted Textual app."""

    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []
        self.render_calls = 0

    def post_message(self, message):  # type: ignore[override]
        self.messages.append(message.to_mode)
        return True

    def _render_mode(self) -> None:  # type: ignore[override]
        self.render_calls += 1


def test_bindings_include_picker_controls() -> None:
    key_by_action = {binding.action: binding.key for binding in ModeStrip.BINDINGS}
    assert key_by_action["open_mode_picker"] == "m"
    assert key_by_action["picker_left"] == "left"
    assert key_by_action["picker_right"] == "right"
    assert key_by_action["picker_confirm"] == "enter"
    assert key_by_action["picker_cancel"] == "escape"


def test_open_picker_sets_index_to_current_mode() -> None:
    strip = _FakeModeStrip()
    strip._mode = "autorun_ready"
    strip.action_open_mode_picker()
    assert strip._picker_open is True
    assert strip._picker_index == 1


def test_left_and_right_wrap_when_picker_open() -> None:
    strip = _FakeModeStrip()
    strip.action_open_mode_picker()
    strip.action_picker_left()
    assert strip._picker_index == 2
    strip.action_picker_right()
    assert strip._picker_index == 0


def test_enter_confirms_selected_mode_and_closes_picker() -> None:
    strip = _FakeModeStrip()
    strip.action_open_mode_picker()
    strip.action_picker_right()
    strip.action_picker_confirm()
    assert strip._picker_open is False
    assert strip.messages == ["autorun_ready"]


def test_escape_cancels_picker_without_emitting_mode_change() -> None:
    strip = _FakeModeStrip()
    strip.action_open_mode_picker()
    strip.action_picker_right()
    strip.action_picker_cancel()
    assert strip._picker_open is False
    assert strip.messages == []

