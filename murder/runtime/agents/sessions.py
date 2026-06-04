"""Durable and live agent session types."""

from __future__ import annotations

import asyncio
import contextlib
import sys
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

if sys.version_info >= (3, 11):
    from enum import StrEnum
else:

    class StrEnum(str, Enum):
        def __str__(self) -> str:
            return str.__str__(self)


class AgentSessionStatus(StrEnum):
    STARTING = "starting"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


@dataclass(frozen=True, slots=True)
class AgentScope:
    """What an agent session is about. All fields optional — global agents (e.g. Sentinel) have no ticket/plan/worktree."""

    ticket_id: str | None = None
    plan_name: str | None = None
    worktree_path: str | None = None


@dataclass(frozen=True, slots=True)
class AgentSpec:
    """Everything needed to spawn an agent session."""

    role: str
    scope: AgentScope
    harness: str | None = None
    model: str | None = None
    effort: str | None = None
    startup_prompt: str | None = None
    escalation_target: str | None = None
    capabilities_required: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "capabilities_required",
            frozenset(self.capabilities_required),
        )


@dataclass(frozen=True, slots=True)
class AgentHandle:
    """Live reference to a running agent session."""

    agent_id: str
    session_name: str
    spec: AgentSpec
    task: asyncio.Task[object] | None = None

    async def interrupt(self) -> None:
        if self.task is None or self.task.done():
            return
        self.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self.task

    async def is_live(self) -> bool:
        task = self.task
        return task is not None and not task.done() and not task.cancelled()


@dataclass(frozen=True, slots=True)
class AgentSession:
    """Durable record of an agent session."""

    session_id: str
    spec: AgentSpec
    status: AgentSessionStatus
    started_at: datetime
    ended_at: datetime | None = None


__all__ = [
    "AgentHandle",
    "AgentScope",
    "AgentSession",
    "AgentSessionStatus",
    "AgentSpec",
]
