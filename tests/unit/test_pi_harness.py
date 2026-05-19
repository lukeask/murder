from __future__ import annotations

from pathlib import Path

from murder.harnesses.pi_harness import PiAdapter
from tests.unit._harness_assertions import assert_adapter_basics


def test_startup_cmd_includes_model_when_configured() -> None:
    assert PiAdapter(startup_model="anthropic/claude-sonnet-4-6").startup_cmd(Path("/repo")) == [
        "pi",
        "--model",
        "anthropic/claude-sonnet-4-6",
    ]


def test_ready_idle_pane() -> None:
    pane = """
Pi

Loaded AGENTS.md
/hotkeys for all shortcuts
Ctrl+L model selector · Ctrl+P cycle models
"""
    adapter = PiAdapter()
    assert adapter.is_ready(pane)
    assert adapter.is_idle(pane)
    assert not adapter.is_busy(pane)


def test_auth_prompt_blocks_ready() -> None:
    pane = "Authenticate with an API key or run /login"
    adapter = PiAdapter()
    assert not adapter.is_ready(pane)
    assert not adapter.is_idle(pane)
    assert not adapter.is_busy(pane)


def test_busy_marker_blocks_idle() -> None:
    pane = "/hotkeys for all shortcuts\nThinking\n"
    adapter = PiAdapter()
    assert adapter.is_ready(pane)
    assert adapter.is_busy(pane)
    assert not adapter.is_idle(pane)


def test_pi_adapter_contract_basics() -> None:
    pane = "/hotkeys for all shortcuts\n"
    assert_adapter_basics(PiAdapter(), pane, Path("/repo"))


async def test_pi_usage_status_unsupported() -> None:
    result = await PiAdapter().attach("sess", Path("/repo")).request_usage_status()
    assert not result.ok
