"""Unit tests for the ACP agent registry and Cursor profile."""

from __future__ import annotations

import pytest

from murder.llm.harness_control.acp import agents as agents_mod
from murder.llm.harness_control.acp.agents import (
    AcpAgentProfile,
    get_agent,
    get_agent_for_harness,
    list_agents,
    register_agent,
)
from murder.llm.harness_control.acp.agents.cursor import PROFILE as CURSOR_PROFILE
from murder.llm.harness_control.acp.bootstrap import (
    placeholder_cmd_for_profile,
    resolve_agent_profile,
    uses_acp_backend,
)


def test_cursor_profile_registered() -> None:
    agent = get_agent("cursor")
    assert agent is CURSOR_PROFILE
    assert agent.agent_id == "cursor"
    assert agent.harness_kind == "cursor"
    assert agent.argv == ("agent", "acp")
    assert agent.auth_method_id == "cursor_login"
    assert "fs" in agent.client_capabilities
    assert "cursor/ask_question" in agent.blocking_extension_methods
    assert "cursor/update_todos" in agent.notification_extension_methods


def test_get_agent_for_harness_cursor() -> None:
    assert get_agent_for_harness("cursor") is CURSOR_PROFILE
    assert get_agent_for_harness("codex") is None


def test_list_agents_includes_cursor() -> None:
    agents = list_agents()
    assert any(profile.agent_id == "cursor" for profile in agents)


def test_register_agent_onboards_new_profile() -> None:
    """Documenting that a new file's PROFILE can be registered at runtime.

    Production onboarding: add ``agents/<name>.py`` with a PROFILE and import
    it in ``agents/__init__.py`` so registration runs at import time.
    """
    profile = AcpAgentProfile(
        agent_id="example-test-agent",
        harness_kind="example-test",
        argv=("example-agent", "acp"),
        auth_method_id=None,
        placeholder_cmd=("bash", "-lc", "sleep infinity"),
    )
    register_agent(profile)
    try:
        assert get_agent("example-test-agent") is profile
        assert get_agent_for_harness("example-test") is profile
        assert uses_acp_backend(harness_kind="example-test", backend="acp") is True
        assert uses_acp_backend(harness_kind="example-test", backend="tmux") is False
        assert placeholder_cmd_for_profile(profile) == [
            "bash",
            "-lc",
            "sleep infinity",
        ]
    finally:
        # Avoid leaking into other tests that may list agents.
        agents_mod._REGISTRY.pop("example-test-agent", None)
        agents_mod._BY_HARNESS.pop("example-test", None)


def test_get_agent_unknown_raises() -> None:
    with pytest.raises(KeyError, match="unknown ACP agent"):
        get_agent("does-not-exist")


def test_uses_acp_backend_for_cursor() -> None:
    assert uses_acp_backend(harness_kind="cursor", backend="acp") is True
    assert uses_acp_backend(harness_kind="cursor", backend="app_server") is False
    assert uses_acp_backend(harness_kind="codex", backend="acp") is False


def test_resolve_agent_profile() -> None:
    assert resolve_agent_profile("cursor") is CURSOR_PROFILE
    assert resolve_agent_profile(CURSOR_PROFILE) is CURSOR_PROFILE
