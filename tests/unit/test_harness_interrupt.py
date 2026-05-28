"""Per-adapter interrupt() sends the harness-specific tmux key sequence."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from murder.harnesses.claude_code import ClaudeCodeAdapter
from murder.harnesses.codex import CodexAdapter
from murder.harnesses.cursor import CursorAdapter
from murder.harnesses.pi_harness import PiAdapter

FIXTURES = Path(__file__).parent.parent / "fixtures" / "harness_panes"


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


def test_interrupt_fixtures_exist():
    for name in (
        "cc_interrupt.txt",
        "codex_interrupt.txt",
        "cursor_interrupt.txt",
        "pi_interrupt.txt",
    ):
        assert (FIXTURES / name).is_file(), f"missing fixture {name}"
