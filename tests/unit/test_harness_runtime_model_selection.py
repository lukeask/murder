from __future__ import annotations

import asyncio

from murder.harnesses.claude_code import ClaudeCodeAdapter
from murder.harnesses.codex import CodexAdapter
from tests.support.fake_tmux import FakeTmux

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
    fake_tmux.queue_pane(CODEX_IDLE_FOO_MEDIUM)
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
