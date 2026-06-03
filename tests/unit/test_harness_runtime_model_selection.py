from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from murder.harnesses.antigravity import AntigravityAdapter
from murder.harnesses.claude_code import ClaudeCodeAdapter
from murder.harnesses.codex import CodexAdapter
from murder.harnesses.cursor import CursorAdapter
from murder.harnesses.pi_harness import PiAdapter
from murder.harnesses.parsing import parse_antigravity_model_choices
from tests.support.fake_tmux import FakeTmux

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "harness_panes"


def _pane(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding="utf-8")

CC_MENU_OPUS_HIGH = """
Select Model

  1. Default (recommended)  Sonnet 4.6
  2. Sonnet (1M context)   Sonnet 4.6 with 1M context
> 3. Opus ✓                Opus 4.8 · Most capable for complex work
  4. Haiku                 Haiku 4.5

● High effort (default) ←/→ to adjust
"""

CC_IDLE_OPUS_MEDIUM = """
 ▐▛███▜▌   Claude Code v2.1.150
▝▜█████▛▘  Opus 4.8 with medium effort · Claude Pro
  ▘▘ ▝▝    ~/Documents/code/murder

❯ Try "create a util logging.py that..."
  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents             ● medium · /effort
"""

CODEX_MODEL_PICKER = """
Select model and effort

1. gpt-5.5 (current)  Frontier model
2. modelnamebar       helptext here
3. modelnamefoo       helptext for foo here
4. modelnamefoobar    helptext
5. gpt-5.2            Older model
"""

CODEX_REASONING_PICKER = """
Select Reasoning Level for modelnamefoo

1. Low
2. Medium (default)
3. High
4. Extra High
"""

CODEX_IDLE_FOO_MEDIUM = """
› Find and fix a bug in @filename

  modelnamefoo medium · ~/Documents/code/murder
"""

CODEX_IDLE_MINI_NO_EFFORT = """
› Find and fix a bug in @filename

  model: gpt-5.4-mini · ~/Documents/code/murder
"""

CODEX_IDLE_MINI_LOW = """
› Find and fix a bug in @filename

  gpt-5.4-mini low · ~/Documents/code/murder
"""


def test_cc_active_model_state_from_chat_input() -> None:
    state = ClaudeCodeAdapter().parse_active_model_state(CC_IDLE_OPUS_MEDIUM)

    assert state is not None
    assert state.model == "opus"
    assert state.effort == "medium"


def test_codex_active_model_state_from_bottom_left() -> None:
    state = CodexAdapter().parse_active_model_state(CODEX_IDLE_FOO_MEDIUM)

    assert state is not None
    assert state.model == "modelnamefoo"
    assert state.effort == "medium"


def test_codex_active_model_state_allows_missing_effort() -> None:
    state = CodexAdapter().parse_active_model_state(CODEX_IDLE_MINI_NO_EFFORT)

    assert state is not None
    assert state.model == "gpt-5.4-mini"
    assert state.effort is None


def test_cc_set_model_selects_model_and_adjusts_effort(fake_tmux: FakeTmux) -> None:
    fake_tmux.queue_pane(CC_MENU_OPUS_HIGH)
    fake_tmux.queue_pane(CC_IDLE_OPUS_MEDIUM)

    ok = asyncio.run(ClaudeCodeAdapter().set_model("sess", "opus", effort="medium"))

    assert ok is True
    send_calls = fake_tmux.calls_to("send_keys")
    sent = [(args[1], kwargs) for args, kwargs in send_calls]
    assert ("/model opus", {"literal": True, "enter": True}) in sent
    assert ("Left", {"literal": False, "enter": False}) in sent
    assert ("", {"literal": True, "enter": True}) in sent


def test_codex_set_model_uses_picker_indices_then_reasoning_index(fake_tmux: FakeTmux) -> None:
    fake_tmux.queue_pane(CODEX_IDLE_FOO_MEDIUM.replace("modelnamefoo", "modelnamebar"))
    fake_tmux.queue_pane(CODEX_IDLE_FOO_MEDIUM.replace("modelnamefoo", "modelnamebar"))
    fake_tmux.queue_pane(CODEX_MODEL_PICKER)
    fake_tmux.queue_pane(CODEX_MODEL_PICKER)
    fake_tmux.queue_pane(CODEX_REASONING_PICKER)
    fake_tmux.queue_pane(CODEX_IDLE_FOO_MEDIUM)

    ok = asyncio.run(CodexAdapter().set_model("sess", "modelnamefoo", effort="medium"))

    assert ok is True
    send_calls = fake_tmux.calls_to("send_keys")
    sent = [(args[1], kwargs) for args, kwargs in send_calls]
    assert ("/model", {"literal": True, "enter": False}) in sent
    assert ("3", {"literal": True, "enter": False}) in sent
    assert ("2", {"literal": True, "enter": False}) in sent


def test_codex_set_model_accepts_existing_startup_model(fake_tmux: FakeTmux) -> None:
    fake_tmux.queue_pane(CODEX_IDLE_FOO_MEDIUM)

    ok = asyncio.run(CodexAdapter().set_model("sess", "modelnamefoo", effort="medium"))

    assert ok is True
    sent = [args[1] for args, _ in fake_tmux.calls_to("send_keys")]
    assert "/model" not in sent


def test_codex_set_model_accepts_existing_model_when_effort_missing(fake_tmux: FakeTmux) -> None:
    fake_tmux.queue_pane(CODEX_IDLE_MINI_NO_EFFORT)

    ok = asyncio.run(CodexAdapter().set_model("sess", "gpt-5.4-mini", effort="medium"))

    assert ok is True
    sent = [args[1] for args, _ in fake_tmux.calls_to("send_keys")]
    assert "/model" not in sent


def test_codex_set_model_trusts_startup_model_when_picker_renders_no_choices(
    fake_tmux: FakeTmux,
) -> None:
    # Active model differs from desired, so the picker is attempted — but it
    # never renders any numbered rows. The launch --model flag already selected
    # the startup model, so we trust it (effort stays best-effort) rather than
    # failing startup on a flaky picker.
    fake_tmux.queue_pane(CODEX_IDLE_FOO_MEDIUM)  # active = modelnamefoo ≠ desired

    ok = asyncio.run(
        CodexAdapter(startup_model="gpt-5.4-mini").set_model(
            "sess", "gpt-5.4-mini", effort="medium"
        )
    )

    assert ok is True
    sent = [args[1] for args, _ in fake_tmux.calls_to("send_keys")]
    assert "/model" in sent  # picker IS attempted now (not blanket-trusted)


def test_codex_set_model_trusts_startup_model_when_choice_absent(fake_tmux: FakeTmux) -> None:
    # Picker renders, but the desired startup model isn't among the listed rows;
    # dismiss the picker and fall back to trusting the launch flag.
    fake_tmux.queue_pane(CODEX_IDLE_FOO_MEDIUM)  # active ≠ desired
    fake_tmux.queue_pane(CODEX_IDLE_FOO_MEDIUM)  # idle, for request_model_list
    fake_tmux.queue_pane(CODEX_MODEL_PICKER)  # no gpt-5.4-mini row
    fake_tmux.queue_pane(CODEX_MODEL_PICKER)

    ok = asyncio.run(
        CodexAdapter(startup_model="gpt-5.4-mini").set_model(
            "sess", "gpt-5.4-mini", effort="medium"
        )
    )

    assert ok is True
    sent = [args[1] for args, _ in fake_tmux.calls_to("send_keys")]
    assert "Escape" in sent  # picker dismissed before the fallback


CODEX_IDLE_55_MEDIUM = """
› Find and fix a bug in @filename

  gpt-5.5 medium · ~/Documents/code/murder
"""

CODEX_IDLE_55_HIGH = """
› Find and fix a bug in @filename

  gpt-5.5 high · ~/Documents/code/murder
"""

CODEX_PICKER_55 = """
Select model and effort

1. gpt-5.5 (current)  Frontier model
2. gpt-5.4            Older model
"""


def test_codex_set_model_applies_nondefault_effort_to_startup_model(
    fake_tmux: FakeTmux,
) -> None:
    # The bug this guards: launching codex with --model gpt-5.5 selects the
    # model but leaves effort at the default. Asking for gpt-5.5 *high* must
    # drive the reasoning picker, not silently leave it on medium.
    fake_tmux.queue_pane(CODEX_IDLE_55_MEDIUM)  # active gpt-5.5 medium ≠ high
    fake_tmux.queue_pane(CODEX_IDLE_55_MEDIUM)  # idle, for request_model_list
    fake_tmux.queue_pane(CODEX_PICKER_55)  # picker renders gpt-5.5
    fake_tmux.queue_pane(CODEX_PICKER_55)
    fake_tmux.queue_pane(CODEX_REASONING_PICKER)  # 3. High
    fake_tmux.queue_pane(CODEX_IDLE_55_HIGH)  # verified: gpt-5.5 high

    ok = asyncio.run(
        CodexAdapter(startup_model="gpt-5.5").set_model("sess", "gpt-5.5", effort="high")
    )

    assert ok is True
    sent = [args[1] for args, _ in fake_tmux.calls_to("send_keys")]
    assert "/model" in sent
    assert "1" in sent  # gpt-5.5 row index
    assert "3" in sent  # High reasoning index — effort actually applied


def test_codex_set_model_trusts_startup_model_after_picker_mismatch(fake_tmux: FakeTmux) -> None:
    fake_tmux.queue_pane(CODEX_IDLE_FOO_MEDIUM)
    fake_tmux.queue_pane(CODEX_IDLE_FOO_MEDIUM)
    fake_tmux.queue_pane(CODEX_MODEL_PICKER.replace("modelnamefoo", "gpt-5.4-mini"))
    fake_tmux.queue_pane(CODEX_MODEL_PICKER.replace("modelnamefoo", "gpt-5.4-mini"))
    fake_tmux.queue_pane(CODEX_REASONING_PICKER)
    fake_tmux.queue_pane(CODEX_IDLE_FOO_MEDIUM)

    ok = asyncio.run(
        CodexAdapter(startup_model="gpt-5.4-mini").set_model(
            "sess", "gpt-5.4-mini", effort="medium"
        )
    )

    assert ok is True


CURSOR_COMPOSER_MENU_SLOW = """
Available models

 → Composer 2.5             Slow (Tab to modify)
   Composer 2
   GPT-5.5                  272K Medium
"""

CURSOR_COMPOSER_EDIT_PARAMETERS_SLOW = """
Available models

 Composer 2.5 — Edit Parameters

 → [ ] Fast

 Enter to confirm • Esc to cancel
"""

CURSOR_COMPOSER_EDIT_PARAMETERS_FAST = """
Available models

 Composer 2.5 — Edit Parameters

 → [x] Fast

 Enter to confirm • Esc to cancel
"""

CURSOR_IDLE_COMPOSER_FAST = """
  → Plan, search, build anything
  Composer 2.5 Fast                                                              Auto-run
  ~/Documents/code/murder · main
"""

CURSOR_IDLE_COMPOSER_NO_SPEED = """
  → Plan, search, build anything
  Composer 2.5                                                                   Auto-run
  ~/Documents/code/murder · main
"""

CURSOR_MODEL_LIST_PAGE1 = _pane("cursor_model_list.txt")
CURSOR_MODEL_LIST_PAGE2 = """
Available models

 Filter:

   Kimi K2.5
   Grok 4

 11-20 of 27

 Type to filter • Enter to select • Tab to edit
"""
CURSOR_MODEL_LIST_PAGE3 = """
Available models

 Filter:

   o3
   o4-mini

 21-27 of 27

 Type to filter • Enter to select • Tab to edit
"""

AGY_MODEL_PICKER = _pane("agy_model_picker.txt")
AGY_IDLE = _pane("agy_idle.txt")
PI_MODEL_PICKER = _pane("pi_model_picker.txt")
PI_IDLE = """
~/Documents/code/testingmurderharness
0.0%/1.0M (auto)                             (deepseek) deepseek-v4-flash • high
"""


@pytest.mark.asyncio
async def test_cursor_collect_available_models_scrolls_pages(fake_tmux: FakeTmux) -> None:
    fake_tmux.queue_pane(CURSOR_MODEL_LIST_PAGE1)
    fake_tmux.queue_pane(CURSOR_MODEL_LIST_PAGE2)
    fake_tmux.queue_pane(CURSOR_MODEL_LIST_PAGE3)
    fake_tmux.queue_pane(CURSOR_MODEL_LIST_PAGE3)

    result = await CursorAdapter().collect_available_models("sess")

    assert result.ok, result.message
    assert result.data is not None
    ids = {model_id for model_id, _ in result.data}
    assert len(ids) >= 12
    assert "composer-2.5" in ids
    assert "kimi-k2-5" in ids
    assert "o3" in ids
    sent = [args[1] for args, _ in fake_tmux.calls_to("send_keys")]
    assert "/model" in sent
    assert "PageDown" in sent
    assert "Escape" in sent


def test_cursor_default_effort_is_slow_not_fast() -> None:
    assert CursorAdapter.default_effort == "slow"
    assert "fast" in CursorAdapter.supported_efforts


def test_cursor_active_model_state_parses_composer_speed() -> None:
    state = CursorAdapter().parse_active_model_state(CURSOR_IDLE_COMPOSER_FAST)

    assert state is not None
    assert state.model == "composer-2.5"
    assert state.effort == "fast"


@pytest.mark.asyncio
async def test_cursor_set_model_accepts_current_default_composer_without_opening_picker(
    fake_tmux: FakeTmux,
) -> None:
    fake_tmux.queue_pane(CURSOR_IDLE_COMPOSER_NO_SPEED)

    ok = await CursorAdapter().set_model("sess", "composer-2.5", effort="slow")

    assert ok
    sent = [args[1] for args, _ in fake_tmux.calls_to("send_keys")]
    assert "/model" not in sent


@pytest.mark.asyncio
async def test_cursor_set_model_waits_briefly_for_default_composer_footer(
    fake_tmux: FakeTmux,
) -> None:
    fake_tmux.queue_pane("")
    fake_tmux.queue_pane(CURSOR_IDLE_COMPOSER_NO_SPEED)

    ok = await CursorAdapter().set_model("sess", "composer-2.5", effort="slow")

    assert ok
    sent = [args[1] for args, _ in fake_tmux.calls_to("send_keys")]
    assert "/model" not in sent


@pytest.mark.asyncio
async def test_cursor_set_composer_speed_tabs_until_slow(fake_tmux: FakeTmux) -> None:
    idle_slow = CURSOR_IDLE_COMPOSER_FAST.replace("Fast", "Slow")
    fake_tmux.queue_pane(CURSOR_COMPOSER_MENU_SLOW)
    fake_tmux.queue_pane(idle_slow)
    fake_tmux.queue_pane(idle_slow)

    ok = await CursorAdapter().set_model("sess", "composer-2.5", effort="slow")

    assert ok
    sent = [args[1] for args, _ in fake_tmux.calls_to("send_keys")]
    assert "/model" in sent
    assert "Composer 2.5" in sent


@pytest.mark.asyncio
async def test_cursor_set_composer_speed_accepts_checkbox_ui_for_slow(
    fake_tmux: FakeTmux,
) -> None:
    fake_tmux.queue_pane(CURSOR_COMPOSER_EDIT_PARAMETERS_SLOW)
    fake_tmux.queue_pane(CURSOR_IDLE_COMPOSER_NO_SPEED)

    ok = await CursorAdapter().set_model("sess", "composer-2.5", effort="slow")

    assert ok
    sent = [args[1] for args, _ in fake_tmux.calls_to("send_keys")]
    assert "/model" in sent
    assert "Composer 2.5" in sent
    assert "Space" not in sent


@pytest.mark.asyncio
async def test_cursor_set_composer_speed_toggles_checkbox_ui_for_fast(
    fake_tmux: FakeTmux,
) -> None:
    fake_tmux.queue_pane(CURSOR_IDLE_COMPOSER_NO_SPEED)
    fake_tmux.queue_pane(CURSOR_COMPOSER_EDIT_PARAMETERS_SLOW)
    fake_tmux.queue_pane(CURSOR_COMPOSER_EDIT_PARAMETERS_FAST)
    fake_tmux.queue_pane(CURSOR_IDLE_COMPOSER_FAST)

    ok = await CursorAdapter().set_model("sess", "composer-2.5", effort="fast")

    assert ok
    sent = [args[1] for args, _ in fake_tmux.calls_to("send_keys")]
    assert "/model" in sent
    assert "Composer 2.5" in sent
    assert "Space" in sent


def test_antigravity_model_choices_include_effort_tags() -> None:
    choices = parse_antigravity_model_choices(AGY_MODEL_PICKER)
    current = [choice for choice in choices if choice.current]
    assert current
    assert current[0].model_id == "gemini-3-1-pro"


def test_antigravity_active_model_state_from_status_line() -> None:
    state = AntigravityAdapter().parse_active_model_state(AGY_IDLE)

    assert state is not None
    assert state.model == "gemini-3-1-pro"
    assert state.effort == "low"


def test_antigravity_set_model_navigates_picker(fake_tmux: FakeTmux) -> None:
    fake_tmux.queue_pane(AGY_MODEL_PICKER)
    fake_tmux.queue_pane(AGY_IDLE.replace("Gemini 3.1 Pro (Low)", "Gemini 3.5 Flash (Medium)"))

    ok = asyncio.run(
        AntigravityAdapter().set_model("sess", "gemini-3-5-flash", effort="medium")
    )

    assert ok is True
    sent = [args[1] for args, _ in fake_tmux.calls_to("send_keys")]
    assert "/model" in sent
    assert "Up" in sent


def test_pi_active_model_state_from_status_bar() -> None:
    state = PiAdapter().parse_active_model_state(PI_IDLE)

    assert state is not None
    assert state.model == "deepseek/deepseek-v4-flash"
    assert state.effort == "high"


def test_pi_set_model_filters_picker_then_confirms(fake_tmux: FakeTmux) -> None:
    fake_tmux.queue_pane(PI_MODEL_PICKER)
    fake_tmux.queue_pane(PI_IDLE)

    ok = asyncio.run(PiAdapter().set_model("sess", "deepseek/deepseek-v4-flash"))

    assert ok is True
    sent = [args[1] for args, _ in fake_tmux.calls_to("send_keys")]
    assert "/model" in sent
    assert "deepseek-v4-flash" in sent
