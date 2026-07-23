"""The cast: Collaborator, Notetaker, PlanningAgent, CrowHandler, Crow.

See `.murder/1777410436NOTES.md` for role responsibilities and the
hierarchy: User → Collaborator → PlanningAgent (per-plan) → CrowHandler → Crow.
Planning capture: bus `notetaker.capture.submit` → orchestrator →
`murder.work.notes.submit_capture`.
"""

from murder.runtime.agents.base import Daemon, HarnessBackedAgent, LifecycleParticipant
from murder.runtime.agents.types import AgentRole, AgentStatus

__all__ = [
    "LifecycleParticipant",
    "HarnessBackedAgent",
    "Daemon",
    "AgentRole",
    "AgentStatus",
]
