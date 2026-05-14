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


def test_startup_cmd_includes_model_when_configured() -> None:
    cmd = CodexAdapter(startup_model="gpt-5.5").startup_cmd(Path("/repo"))
    assert cmd[-2:] == ["--model", "gpt-5.5"]


async def test_codex_startup_model_is_supported_after_startup() -> None:
    result = await CodexAdapter(startup_model="gpt-5.5").attach("sess", Path("/repo")).set_model(
        "gpt-5.5"
    )
    assert result.ok


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


def test_idle_prompt_above_blank_screen_lines_is_idle() -> None:
    pane = """
╭──────────────────────────────────────────────╮
│ >_ OpenAI Codex (v0.130.0)                   │
╰──────────────────────────────────────────────╯

› Explain this codebase

  gpt-5.5 high · ~/Agents/projects/graphvisexplore































"""
    adapter = CodexAdapter()
    assert adapter.is_ready(pane)
    assert adapter.is_idle(pane)
    assert not adapter.is_busy(pane)


def test_startup_tip_mentioning_running_is_not_busy() -> None:
    pane = """
╭──────────────────────────────────────────────╮
│ >_ OpenAI Codex (v0.130.0)                   │
╰──────────────────────────────────────────────╯

  Tip: NEW: Prevent sleep while running is now available in /experimental.


› Implement {feature}

  gpt-5.5 high · ~/Agents/projects/graphvisexplore
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
    sleeps: list[float] = []

    async def fake_send_keys(
        session: str, text: str, *, literal: bool = True, enter: bool = True
    ) -> None:
        calls.append((session, text, literal, enter))

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("murder.tmux.send_keys", fake_send_keys)
    monkeypatch.setattr("murder.harnesses.codex.asyncio.sleep", fake_sleep)
    result = await CodexAdapter().attach("sess", Path("/repo")).request_usage_status()
    assert result.ok
    assert calls == [
        ("sess", "/status", True, False),
        ("sess", "", True, True),
        ("sess", "", True, True),
    ]
    assert sleeps == [0.5, 0.8, 1.2]


async def test_codex_send_prompt_submits_with_tab(monkeypatch) -> None:
    calls: list[tuple[str, str, bool, bool]] = []
    sleeps: list[float] = []

    async def fake_send_keys(
        session: str, text: str, *, literal: bool = True, enter: bool = True
    ) -> None:
        calls.append((session, text, literal, enter))

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("murder.tmux.send_keys", fake_send_keys)
    monkeypatch.setattr("murder.harnesses.codex.asyncio.sleep", fake_sleep)
    result = await CodexAdapter().attach("sess", Path("/repo")).send_prompt("hello")
    assert result.ok
    assert calls == [
        ("sess", "hello", True, False),
        ("sess", "Tab", False, False),
    ]
    assert sleeps == [0.2]


async def test_codex_request_model_list_waits_for_slash_picker(monkeypatch) -> None:
    calls: list[tuple[str, str, bool, bool]] = []
    sleeps: list[float] = []

    async def fake_send_keys(
        session: str, text: str, *, literal: bool = True, enter: bool = True
    ) -> None:
        calls.append((session, text, literal, enter))

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("murder.tmux.send_keys", fake_send_keys)
    monkeypatch.setattr("murder.harnesses.codex.asyncio.sleep", fake_sleep)
    result = await CodexAdapter().request_model_list("sess")
    assert result is True
    assert calls == [
        ("sess", "/model", True, False),
        ("sess", "", True, True),
    ]
    assert sleeps == [1.5, 0.5, 3.0]


async def test_codex_collect_usage_status_sends_and_parses_panel(monkeypatch) -> None:
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
        return """
  Usage
  Weekly limit          42% used
  Resets 9:15am (America/New_York)
"""

    monkeypatch.setattr("murder.tmux.send_keys", fake_send_keys)
    monkeypatch.setattr("murder.tmux.capture_pane", fake_capture_pane)
    monkeypatch.setattr("murder.harnesses.codex.asyncio.sleep", fake_sleep)
    result = await CodexAdapter().attach("sess", Path("/repo")).collect_usage_status()
    assert result.ok
    assert result.data is not None
    assert result.data.windows[0].name == "weekly"
    assert result.data.windows[0].percent_used == 42.0
    assert calls == [
        ("sess", "/status", True, False),
        ("sess", "", True, True),
        ("sess", "", True, True),
    ]
    assert sleeps == [0.5, 0.8, 1.2]
