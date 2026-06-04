"""The cast: Collaborator, Notetaker, PlanningAgent, CrowHandler, Crow.

See `.murder/1777410436NOTES.md` for role responsibilities and the
hierarchy: User → Collaborator → PlanningAgent (per-plan) → CrowHandler → Crow.
Planning capture: bus `notetaker.capture.submit` → orchestrator →
`murder.work.notes.submit_capture`.
"""

from murder.runtime.agents.base import LifecycleParticipant, HarnessBackedAgent, Daemon, AgentRole, AgentStatus
from murder.runtime.agents.collaborator import CollaboratorAgent
from murder.runtime.agents.crow import CrowAgent
from murder.runtime.agents.crow_handler import CrowHandler
from murder.runtime.agents.planning_agent import PlanningAgent
from murder.runtime.agents.planning_handler import PlanningHandler

__all__ = [
    "LifecycleParticipant",
    "HarnessBackedAgent",
    "Daemon",
    "AgentRole",
    "AgentStatus",
    "CollaboratorAgent",
    "CrowAgent",
    "CrowHandler",
    "PlanningAgent",
    "PlanningHandler",
]
