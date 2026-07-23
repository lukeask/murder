"""Domain types for runtime-managed agents.

These enums describe agents themselves; orchestration notifications merely
refer to them and must not own or re-export them.
"""

from __future__ import annotations

from enum import Enum


class _StringEnum(str, Enum):
    """Python-3.10-compatible string enum."""

    def __str__(self) -> str:
        return str.__str__(self)


class AgentRole(_StringEnum):
    COLLABORATOR = "collaborator"
    NOTETAKER = "notetaker"
    PLANNER = "planner"
    PLANNING_HANDLER = "planning_handler"
    CROW_HANDLER = "crow_handler"
    CROW = "crow"


class AgentStatus(_StringEnum):
    IDLE = "idle"
    RUNNING = "running"
    BLOCKED = "blocked"
    ESCALATING = "escalating"
    DONE = "done"
    FAILED = "failed"
    DEAD = "dead"


__all__ = ["AgentRole", "AgentStatus"]
