"""Wizard behavior for live transcript multiple-choice prompts."""

from __future__ import annotations

from murder.app.tui.cc_multiple_choice_wizard import CCMultipleChoiceWizard
from murder.llm.harnesses.choice_prompt import ChoiceOption, MultipleChoicePrompt


def _make_wizard() -> CCMultipleChoiceWizard:
    prompt = MultipleChoicePrompt(
        question="Which option?",
        options=(
            ChoiceOption(1, "Alpha"),
            ChoiceOption(2, "Beta"),
            ChoiceOption(3, "Gamma"),
        ),
        selected_index=0,
    )
    wizard = CCMultipleChoiceWizard(prompt)
    wizard._refresh_display = lambda: None  # type: ignore[method-assign]
    return wizard


def test_wizard_initial_cursor_matches_selected_index() -> None:
    wizard = _make_wizard()
    assert wizard._cursor == 0


def test_wizard_cursor_down_advances() -> None:
    wizard = _make_wizard()
    wizard.action_cursor_down()
    assert wizard._cursor == 1


def test_wizard_cursor_up_clamps_at_zero() -> None:
    wizard = _make_wizard()
    wizard.action_cursor_up()
    assert wizard._cursor == 0


def test_wizard_cursor_down_clamps_at_end() -> None:
    wizard = _make_wizard()
    wizard._cursor = 2
    wizard.action_cursor_down()
    assert wizard._cursor == 2


def test_wizard_confirm_posts_message() -> None:
    wizard = _make_wizard()
    wizard._cursor = 1
    messages: list[CCMultipleChoiceWizard.Confirmed] = []

    def _capture(msg: CCMultipleChoiceWizard.Confirmed) -> None:
        messages.append(msg)

    wizard.post_message = _capture  # type: ignore[method-assign]
    wizard.action_confirm()

    assert len(messages) == 1
    assert messages[0].option_number == 2
    assert messages[0].label == "Beta"


def test_wizard_cancel_posts_cancelled() -> None:
    wizard = _make_wizard()
    messages: list[object] = []
    wizard.post_message = messages.append  # type: ignore[method-assign]
    wizard.action_cancel()
    assert len(messages) == 1
    assert isinstance(messages[0], CCMultipleChoiceWizard.Cancelled)


def test_wizard_digit_select_jumps_and_confirms() -> None:
    wizard = _make_wizard()
    wizard._cursor = 0
    messages: list[object] = []
    wizard.post_message = messages.append  # type: ignore[method-assign]
    wizard.action_select_digit(3)

    assert wizard._cursor == 2
    assert len(messages) == 1
    assert isinstance(messages[0], CCMultipleChoiceWizard.Confirmed)
    assert messages[0].option_number == 3  # type: ignore[union-attr]


def test_wizard_digit_select_unknown_number_is_noop() -> None:
    wizard = _make_wizard()
    messages: list[object] = []
    wizard.post_message = messages.append  # type: ignore[method-assign]
    wizard.action_select_digit(9)
    assert messages == []
    assert wizard._cursor == 0
