from __future__ import annotations

from pathlib import Path

from murder.llm.harnesses.choice_prompt import parse_claude_code_choice_prompt

FIXTURES = Path(__file__).parent.parent / "fixtures" / "transcripts" / "cc_mc_samples"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_parse_trust_dialog_detects_mc() -> None:
    result = parse_claude_code_choice_prompt(_load("trust_dialog.txt"))
    assert result is not None


def test_parse_trust_dialog_options() -> None:
    result = parse_claude_code_choice_prompt(_load("trust_dialog.txt"))
    assert result is not None
    assert len(result.options) == 2
    assert result.options[0].number == 1
    assert result.options[0].label == "Yes, I trust this folder"
    assert result.options[1].number == 2
    assert result.options[1].label == "No, exit"


def test_parse_trust_dialog_selected_index() -> None:
    result = parse_claude_code_choice_prompt(_load("trust_dialog.txt"))
    assert result is not None
    assert result.selected_index == 0


def test_parse_trust_dialog_footer() -> None:
    result = parse_claude_code_choice_prompt(_load("trust_dialog.txt"))
    assert result is not None
    assert "Enter to confirm" in result.footer


def test_parse_rate_limit_detects_mc() -> None:
    result = parse_claude_code_choice_prompt(_load("rate_limit.txt"))
    assert result is not None


def test_parse_rate_limit_question() -> None:
    result = parse_claude_code_choice_prompt(_load("rate_limit.txt"))
    assert result is not None
    assert "What do you want to do" in result.question


def test_parse_rate_limit_options() -> None:
    result = parse_claude_code_choice_prompt(_load("rate_limit.txt"))
    assert result is not None
    assert len(result.options) == 2
    assert result.options[0].number == 1
    assert "Stop" in result.options[0].label
    assert result.options[1].number == 2
    assert "Upgrade" in result.options[1].label


def test_parse_rate_limit_selected_index() -> None:
    result = parse_claude_code_choice_prompt(_load("rate_limit.txt"))
    assert result is not None
    assert result.selected_index == 0


def test_parse_test_select_detects_mc() -> None:
    result = parse_claude_code_choice_prompt(_load("test_select.txt"))
    assert result is not None


def test_parse_test_select_option_count() -> None:
    result = parse_claude_code_choice_prompt(_load("test_select.txt"))
    assert result is not None
    assert len(result.options) == 6


def test_parse_test_select_descriptions() -> None:
    result = parse_claude_code_choice_prompt(_load("test_select.txt"))
    assert result is not None
    opt_a = result.options[0]
    assert opt_a.number == 1
    assert opt_a.label == "Option A"
    assert "first test option" in opt_a.description


def test_parse_test_select_option_without_description() -> None:
    result = parse_claude_code_choice_prompt(_load("test_select.txt"))
    assert result is not None
    opt5 = next(option for option in result.options if option.number == 5)
    assert opt5.label == "Type something."
    assert opt5.description == ""


def test_parse_test_select_footer() -> None:
    result = parse_claude_code_choice_prompt(_load("test_select.txt"))
    assert result is not None
    assert "Enter to select" in result.footer


def test_parse_idle_cc_pane_returns_none() -> None:
    idle_pane = (
        "user@machine:~/Documents/code/murder $ claude\n"
        " ▐▛███▜▌   Claude Code v2.1.161\n"
        "▝▜█████▛▘  Sonnet 4.6 with high effort · Claude Pro\n"
        "  ▘▘ ▝▝    ~/Documents/code/murder\n"
        "❯\xa0Try \"how does app.py work?\"\n"
        "──────────────────────────────────────────────────\n"
        "  ⏵⏵ bypass permissions on (shift+tab to cycle)\n"
    )
    assert parse_claude_code_choice_prompt(idle_pane) is None


def test_parse_single_option_returns_none() -> None:
    pane = "Some question\n❯ 1. Only choice\nEnter to confirm\n"
    assert parse_claude_code_choice_prompt(pane) is None


def test_parse_no_cursor_marker_returns_none() -> None:
    pane = "Question?\n  1. Option one\n  2. Option two\n"
    assert parse_claude_code_choice_prompt(pane) is None


def test_parse_empty_string_returns_none() -> None:
    assert parse_claude_code_choice_prompt("") is None
