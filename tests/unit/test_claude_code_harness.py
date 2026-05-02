from __future__ import annotations

from pathlib import Path

from murder.harnesses.claude_code import ClaudeCodeAdapter
from tests.unit.harness_contracts import assert_adapter_basics


def test_claude_adapter_contract_basics() -> None:
    pane = "Welcome to Claude\n>"
    assert_adapter_basics(ClaudeCodeAdapter(), pane, Path("/repo"))


async def test_claude_request_usage_status_sends_usage_command(monkeypatch) -> None:
    calls: list[tuple[str, str, bool, bool]] = []

    async def fake_send_keys(
        session: str, text: str, *, literal: bool = True, enter: bool = True
    ) -> None:
        calls.append((session, text, literal, enter))

    monkeypatch.setattr("murder.tmux.send_keys", fake_send_keys)
    result = await ClaudeCodeAdapter().attach("sess", Path("/repo")).request_usage_status()
    assert result.ok
    assert calls == [("sess", "/usage", True, True)]
