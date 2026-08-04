"""Brief assembly concern extracted from the Orchestrator (move-code refactor)."""

from __future__ import annotations

from pathlib import Path

from murder.llm.harnesses import capabilities_for
from murder.runtime.agents.types import AgentRole
from murder.runtime.orchestration.brief import BriefContext, assembler_for

class BriefService:
    """Builds the startup brief for crow/planner/collaborator spawns."""

    def __init__(self, *, repo_root: Path) -> None:
        self._repo_root = repo_root

    def build(
        self,
        *,
        role: AgentRole,
        harness_name: str,
        ticket: dict | None = None,
        plan_name: str | None = None,
    ) -> str:
        ctx = BriefContext(
            role=role,
            repo_root=self._repo_root,
            caps=capabilities_for(harness_name),
            harness_name=harness_name,
            model=None,
            ticket=ticket,
            plan_name=plan_name,
        )
        return assembler_for(ctx).build(ctx)
