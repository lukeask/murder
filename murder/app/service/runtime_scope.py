"""Narrow protocols for modules that previously accepted full ``Runtime`` (W3)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from murder.runtime.agents.events import AgentEventSink
from murder.runtime.orchestration.ports import CommandSubmitter, OrchestrationEventSink

if TYPE_CHECKING:
    from collections.abc import Callable

    from murder.config import Config
    from murder.runtime.agents.base import LifecycleParticipant


class AgentLifecycleHost(Protocol):
    """DB/bus/run identity + agent persistence lookups."""

    repo_root: Path
    db: sqlite3.Connection | None
    orchestration_events: OrchestrationEventSink | None
    command_submitter: CommandSubmitter | None
    run_id: str | None

    def sync_agent(self, agent: LifecycleParticipant) -> None: ...

    def get_crow(self, ticket_id: str) -> LifecycleParticipant | None: ...

    def get_crow_handler(self, ticket_id: str) -> LifecycleParticipant | None: ...


class OrchestratorHost(AgentLifecycleHost, Protocol):
    """Orchestration surface: lifecycle host plus spawn/registry operations."""

    config: Config
    event_sink: AgentEventSink

    def register_agent(self, agent: LifecycleParticipant) -> None: ...

    def get_agent(self, agent_id: str) -> LifecycleParticipant | None: ...

    async def reap(self, agent_id: str) -> None: ...

    def rename_agent(
        self,
        old_agent_id: str,
        new_agent_id: str,
        *,
        persist: Callable[[LifecycleParticipant], None] | None = None,
    ) -> LifecycleParticipant | None: ...


__all__ = [
    "AgentLifecycleHost",
    "OrchestratorHost",
]
