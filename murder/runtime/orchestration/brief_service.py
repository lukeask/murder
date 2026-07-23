"""Brief assembly concern extracted from the Orchestrator (move-code refactor)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from murder.llm.harnesses import capabilities_for
from murder.runtime.agents.types import AgentRole
from murder.runtime.orchestration.brief import BriefContext, assembler_for

if TYPE_CHECKING:
    from murder.app.service.runtime_scope import OrchestratorHost


class BriefService:
    """Builds the startup brief for crow/planner/collaborator spawns."""

    def __init__(self, rt: OrchestratorHost) -> None:
        self.rt = rt

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
            repo_root=self.rt.repo_root,
            caps=capabilities_for(harness_name),
            harness_name=harness_name,
            model=None,
            ticket=ticket,
            plan_name=plan_name,
        )
        return assembler_for(ctx).build(ctx)
