"""In-process agent handles keyed by id and ticket (W3 Runtime narrow)."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from murder.runtime.agents.base import AgentRole
from murder.bus import AgentStatus

if TYPE_CHECKING:
    import sqlite3

    from murder.runtime.agents.base import LifecycleParticipant


# Signature of the optional lifecycle hook Runtime wires onto the registry so
# register / rename / clear ride the one bus aspect (AgentLifecycleEvent into
# agent_records). Keyword-only: op, agent_id, details, reason.
LifecycleHook = Callable[..., None]


class AgentRegistry:
    """Owns live agent instances; Runtime delegates registration and lookup."""

    def __init__(self) -> None:
        self._agents: dict[str, LifecycleParticipant] = {}
        self._crows: dict[str, LifecycleParticipant] = {}
        self._crow_handlers: dict[str, LifecycleParticipant] = {}
        # Set by Runtime.start when the flight recorder is on; the registry has
        # no bus handle of its own, so it calls back through this (no-op when
        # unset, e.g. in tests or below the `advanced` rung).
        self.on_lifecycle: LifecycleHook | None = None

    def _emit(self, *, op: str, agent_id: str, **details: Any) -> None:
        if self.on_lifecycle is not None:
            self.on_lifecycle(op=op, agent_id=agent_id, details=details)

    def register(self, agent: LifecycleParticipant, *, persist: Callable[[LifecycleParticipant], None] | None = None) -> None:
        """Track one agent; optional ``persist`` is ``Runtime.sync_agent``."""
        self._agents[agent.id] = agent
        if agent.ticket_id is not None:
            if agent.role == AgentRole.CROW:
                self._crows[agent.ticket_id] = agent
            elif agent.role == AgentRole.CROW_HANDLER:
                self._crow_handlers[agent.ticket_id] = agent
        self._emit(
            op="register",
            agent_id=agent.id,
            role=getattr(getattr(agent, "role", None), "value", None),
            ticket_id=agent.ticket_id,
        )
        if persist is not None:
            persist(agent)

    def get_agent(self, agent_id: str) -> LifecycleParticipant | None:
        return self._agents.get(agent_id)

    def get_crow(self, ticket_id: str) -> LifecycleParticipant | None:
        return self._crows.get(ticket_id)

    def get_crow_handler(self, ticket_id: str) -> LifecycleParticipant | None:
        return self._crow_handlers.get(ticket_id)

    def rename_agent(
        self,
        old_agent_id: str,
        new_agent_id: str,
        *,
        persist: Callable[[LifecycleParticipant], None] | None = None,
    ) -> LifecycleParticipant | None:
        """Rekey a live agent without stopping it."""
        agent = self._agents.pop(old_agent_id, None)
        if agent is None:
            return None
        agent.id = new_agent_id
        self._agents[new_agent_id] = agent
        self._emit(
            op="rename",
            agent_id=new_agent_id,
            old_agent_id=old_agent_id,
            role=getattr(getattr(agent, "role", None), "value", None),
            ticket_id=agent.ticket_id,
        )
        if persist is not None:
            persist(agent)
        return agent

    def all_agents(self) -> list[LifecycleParticipant]:
        return list(self._agents.values())

    async def reap(
        self,
        agent_id: str,
        *,
        tasks: dict[str, asyncio.Task[None]],
        db: sqlite3.Connection | None,
        set_dead: Callable[[sqlite3.Connection, str, str], None] | None = None,
    ) -> None:
        """Stop and drop one agent; cancel its supervise task if present."""
        agent = self._agents.pop(agent_id, None)
        if agent is None:
            return
        # No AgentLifecycleEvent on reap: nothing reacts to the DEAD transition
        # and reap is already recorded by the private orchestration lifecycle path.
        # Gilding it would only risk mid-teardown over-reaction (plan §2.5.A).
        if agent.ticket_id is not None:
            # Only evict the index slot matching THIS agent's role; a crow and
            # its handler share a ticket_id, so reaping one half must not blow
            # away the other half's still-live index entry.
            if agent.role == AgentRole.CROW:
                self._crows.pop(agent.ticket_id, None)
            elif agent.role == AgentRole.CROW_HANDLER:
                self._crow_handlers.pop(agent.ticket_id, None)
        task = tasks.pop(agent_id, None)
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        with contextlib.suppress(Exception):
            await agent.stop()
        if db is not None and set_dead is not None:
            set_dead(db, agent_id, AgentStatus.DEAD.value)

    def clear(self) -> None:
        self._emit(op="clear", agent_id="", count=len(self._agents))
        self._agents.clear()
        self._crows.clear()
        self._crow_handlers.clear()


__all__ = ["AgentRegistry"]
