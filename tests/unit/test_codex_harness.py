"""Pane-state regex tests for the Codex harness."""

from __future__ import annotations

from pathlib import Path

from murder.harnesses.codex import CodexAdapter
from tests.unit.harness_contracts import assert_adapter_basics


def test_startup_cmd_uses_tmux_capture_friendly_mode() -> None:
    cmd = CodexAdapter().startup_cmd(Path("/repo"))
    assert cmd == [
        "codex",
        "--no-alt-screen",
        "--sandbox",
        "workspace-write",
        "--ask-for-approval",
        "never",
    ]


def test_idle_startup_pane_is_ready_and_idle() -> None:
    pane = """
› Explain this codebase

  gpt-5.5 default · ~/Documents/code/murder

╭───────────────────────────────────────╮
│ >_ OpenAI Codex (v0.128.0)            │
│                                       │
│ model:     gpt-5.5   /model to change │
│ directory: ~/Documents/code/murder    │
╰───────────────────────────────────────╯

› Explain this codebase
"""
    adapter = CodexAdapter()
    assert adapter.is_ready(pane)
    assert adapter.is_idle(pane)
    assert not adapter.is_busy(pane)


def test_busy_marker_in_tail_is_busy_not_idle() -> None:
    pane = """
╭───────────────────────────────────────╮
│ >_ OpenAI Codex (v0.128.0)            │
╰───────────────────────────────────────╯

thinking
running shell command
"""
    adapter = CodexAdapter()
    assert adapter.is_ready(pane)
    assert adapter.is_busy(pane)
    assert not adapter.is_idle(pane)


def test_login_prompt_blocks_readiness() -> None:
    pane = """
╭───────────────────────────────────────╮
│ >_ OpenAI Codex (v0.128.0)            │
╰───────────────────────────────────────╯

Not logged in. Run codex login.
"""
    adapter = CodexAdapter()
    assert not adapter.is_ready(pane)
    assert not adapter.is_idle(pane)
    assert not adapter.is_busy(pane)


def test_codex_adapter_contract_basics() -> None:
    pane = "OpenAI Codex\n› Explain this codebase\n"
    assert_adapter_basics(CodexAdapter(), pane, Path("/repo"))


async def test_codex_request_usage_status_sends_status_command(monkeypatch) -> None:
    calls: list[tuple[str, str, bool, bool]] = []

    async def fake_send_keys(
        session: str, text: str, *, literal: bool = True, enter: bool = True
    ) -> None:
        calls.append((session, text, literal, enter))

    monkeypatch.setattr("murder.tmux.send_keys", fake_send_keys)
    result = await CodexAdapter().attach("sess", Path("/repo")).request_usage_status()
    assert result.ok
    assert calls == [("sess", "/status", True, True)]
