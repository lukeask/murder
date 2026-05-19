from __future__ import annotations

from pathlib import Path

from murder.harnesses.claude_code import ClaudeCodeAdapter
from tests.unit._harness_assertions import assert_adapter_basics


def test_claude_startup_cmd_includes_model_when_configured() -> None:
    assert ClaudeCodeAdapter(startup_model="sonnet").startup_cmd(Path("/repo")) == [
        "claude",
        "--dangerously-skip-permissions",
        "--model",
        "sonnet",
    ]


async def test_claude_startup_model_is_supported_after_startup() -> None:
    session = ClaudeCodeAdapter(startup_model="sonnet").attach("sess", Path("/repo"))
    result = await session.set_model("sonnet")
    assert result.ok


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
    assert calls == [
        ("sess", "Escape", False, False),
        ("sess", "/usage", True, True),
    ]


async def test_claude_collect_usage_status_sends_and_parses_panel(monkeypatch) -> None:
    calls: list[tuple[str, str, bool, bool]] = []

    async def fake_send_keys(
        session: str, text: str, *, literal: bool = True, enter: bool = True
    ) -> None:
        calls.append((session, text, literal, enter))

    async def fake_capture_pane(session: str, lines: int = 200) -> str:
        assert session == "sess"
        assert lines == 160
        return """
  Session
  Usage:                 1 input, 2 output, 3 cache read, 4 cache write

  Current session
  ███████████████████████████▌                       55% used
  Resets 7:20pm (America/New_York)
"""

    monkeypatch.setattr("murder.tmux.send_keys", fake_send_keys)
    monkeypatch.setattr("murder.tmux.capture_pane", fake_capture_pane)
    result = await ClaudeCodeAdapter().attach("sess", Path("/repo")).collect_usage_status()
    assert result.ok
    assert result.data is not None
    assert result.data.windows[0].percent_used == 55.0
    assert result.data.session is not None
    assert result.data.session.input_tokens == 1
    assert calls == [
        ("sess", "Escape", False, False),
        ("sess", "/usage", True, True),
    ]


async def test_claude_collect_usage_status_retries_and_fails_on_empty_panel(monkeypatch) -> None:
    calls: list[tuple[str, str, bool, bool]] = []
    sleeps: list[float] = []

    async def fake_send_keys(
        session: str, text: str, *, literal: bool = True, enter: bool = True
    ) -> None:
        calls.append((session, text, literal, enter))

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    async def fake_capture_pane(session: str, lines: int = 200) -> str:
        assert session == "sess"
        assert lines == 160
        return "Welcome to Claude\n> "

    monkeypatch.setattr("murder.tmux.send_keys", fake_send_keys)
    monkeypatch.setattr("murder.tmux.capture_pane", fake_capture_pane)
    monkeypatch.setattr("murder.harnesses.claude_code.asyncio.sleep", fake_sleep)
    result = await ClaudeCodeAdapter().attach("sess", Path("/repo")).collect_usage_status()
    assert not result.ok
    assert result.message == "claude /usage did not expose any usage details"
    assert calls == [
        ("sess", "Escape", False, False),
        ("sess", "/usage", True, True),
        ("sess", "Escape", False, False),
        ("sess", "/usage", True, True),
    ]
    assert sleeps == [0.1, 0.2, 0.4, 0.4, 0.1, 0.2, 0.4]
