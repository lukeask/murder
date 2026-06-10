"""C14 / V7 — is_rogue_agent_id demoted to a pure, import-light util module.

The TUI imports this from ``runtime.orchestration.agent_ids`` instead of the
heavy ``orchestrator`` module; the orchestrator re-exports it for back-compat.
"""

from __future__ import annotations

from murder.runtime.orchestration.agent_ids import is_rogue_agent_id


def test_rogue_ids_detected() -> None:
    assert is_rogue_agent_id("crow-rogue-x") is True
    assert is_rogue_agent_id("cc-rogue-foo") is True


def test_non_rogue_ids() -> None:
    assert is_rogue_agent_id("crow-t001") is False
    assert is_rogue_agent_id("planner-myplan") is False
    assert is_rogue_agent_id("") is False


def test_orchestrator_reexports_same_function() -> None:
    from murder.runtime.orchestration import orchestrator

    assert orchestrator.is_rogue_agent_id is is_rogue_agent_id
