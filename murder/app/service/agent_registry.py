"""In-process agent indexes keyed by id and ticket role.

Pure index structure: no I/O, no persistence callbacks, no task/stop behavior.
``AgentRuntime`` owns mutations and their durable consequences.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from murder.runtime.agents.types import AgentRole

if TYPE_CHECKING:
    from murder.runtime.agents.base import LifecycleParticipant


class AgentRegistry:
    """Owns live agent indexes only. AgentRuntime owns persistence and lifetime."""

    def __init__(self) -> None:
        self._agents: dict[str, LifecycleParticipant] = {}
        self._crows: dict[str, LifecycleParticipant] = {}
        self._crow_handlers: dict[str, LifecycleParticipant] = {}

    def add(self, agent: LifecycleParticipant) -> None:
        if agent.id in self._agents:
            raise ValueError(f"agent already registered: {agent.id}")
        self._agents[agent.id] = agent
        if agent.ticket_id is not None:
            if agent.role == AgentRole.CROW:
                self._crows[agent.ticket_id] = agent
            elif agent.role == AgentRole.CROW_HANDLER:
                self._crow_handlers[agent.ticket_id] = agent

    def remove(self, agent_id: str) -> LifecycleParticipant | None:
        agent = self._agents.pop(agent_id, None)
        if agent is None:
            return None
        if agent.ticket_id is not None:
            # Only evict the index slot matching THIS agent's role. A crow and
            # its handler share a ticket_id, so reaping one half must not blow
            # away the other half's still-live index entry.
            if agent.role == AgentRole.CROW:
                self._crows.pop(agent.ticket_id, None)
            elif agent.role == AgentRole.CROW_HANDLER:
                self._crow_handlers.pop(agent.ticket_id, None)
        return agent

    def rename(self, old_id: str, new_id: str) -> LifecycleParticipant:
        if old_id == new_id:
            agent = self._agents.get(old_id)
            if agent is None:
                raise KeyError(old_id)
            return agent
        if new_id in self._agents:
            raise ValueError(f"agent already registered: {new_id}")
        agent = self._agents.pop(old_id, None)
        if agent is None:
            raise KeyError(old_id)
        agent.id = new_id
        self._agents[new_id] = agent
        if agent.ticket_id is not None:
            if agent.role == AgentRole.CROW:
                self._crows[agent.ticket_id] = agent
            elif agent.role == AgentRole.CROW_HANDLER:
                self._crow_handlers[agent.ticket_id] = agent
        return agent

    def get(self, agent_id: str) -> LifecycleParticipant | None:
        return self._agents.get(agent_id)

    def get_crow(self, ticket_id: str) -> LifecycleParticipant | None:
        return self._crows.get(ticket_id)

    def get_crow_handler(self, ticket_id: str) -> LifecycleParticipant | None:
        return self._crow_handlers.get(ticket_id)

    def all(self) -> tuple[LifecycleParticipant, ...]:
        return tuple(self._agents.values())

    def clear(self) -> None:
        self._agents.clear()
        self._crows.clear()
        self._crow_handlers.clear()


__all__ = ["AgentRegistry"]
