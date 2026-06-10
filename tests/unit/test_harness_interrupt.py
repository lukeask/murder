"""Per-adapter interrupt() sends Escape via tmux, not Ctrl+C."""

from __future__ import annotations

import asyncio

import pytest

from murder.llm.harnesses.claude_code import ClaudeCodeAdapter
from murder.llm.harnesses.codex import CodexAdapter
from murder.llm.harnesses.cursor import CursorAdapter
from murder.llm.harnesses.pi_harness import PiAdapter


def _escape_interrupt_calls(fake_tmux) -> list[tuple[tuple, dict]]:
    return [
        (args, kw)
        for name, args, kw in fake_tmux.calls
        if name == "send_keys" and len(args) >= 2 and args[1] == "Escape"
    ]


@pytest.mark.parametrize(
    "adapter_cls",
    [ClaudeCodeAdapter, CodexAdapter, CursorAdapter, PiAdapter],
)
def test_interrupt_sends_escape(fake_tmux, adapter_cls):
    adapter = adapter_cls()
    asyncio.run(adapter.interrupt("test-session"))
    calls = _escape_interrupt_calls(fake_tmux)
    assert len(calls) == 1
    (session_arg, keys), kw = calls[0]
    assert session_arg == "test-session"
    assert keys == "Escape"
    assert kw == {"literal": False, "enter": False}
    assert "interrupt" not in fake_tmux.call_names()
