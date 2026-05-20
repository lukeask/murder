"""Narrow protocols for modules that previously accepted full ``Runtime`` (W3)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from murder.bus import Bus
from murder_newstructure.agents.events import AgentEventSink

if TYPE_CHECKING:
    from murder.agents.base import Agent
    from murder.config import Config


class AgentLifecycleHost(Protocol):
    """DB/bus/run identity + agent persistence lookups."""

    repo_root: Path
    db: sqlite3.Connection | None
    bus: Bus | None
    run_id: str | None

    def sync_agent(self, agent: Agent) -> None: ...

    def get_crow(self, ticket_id: str) -> Agent | None: ...

    def get_crow_handler(self, ticket_id: str) -> Agent | None: ...


class OrchestratorHost(AgentLifecycleHost, Protocol):
    """Orchestration surface: lifecycle host plus spawn/registry operations."""

    config: Config
    event_sink: AgentEventSink

    def register_agent(self, agent: Agent) -> None: ...

    def get_agent(self, agent_id: str) -> Agent | None: ...

    async def reap(self, agent_id: str) -> None: ...


__all__ = [
    "AgentLifecycleHost",
    "OrchestratorHost",
]
