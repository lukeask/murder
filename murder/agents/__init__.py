"""The cast: Collaborator, Notetaker, PlanningAgent, CrowHandler, Crow.

See `.murder/1777410436NOTES.md` for role responsibilities and the
hierarchy: User → Collaborator → PlanningAgent (per-plan) → CrowHandler → Crow.
Planning capture: bus `notetaker.capture.submit` → orchestrator →
`murder.notes.submit_capture`.
"""

from murder.agents.base import LifecycleParticipant, HarnessBackedAgent, Daemon, AgentRole, AgentStatus
from murder.agents.collaborator import CollaboratorAgent
from murder.agents.crow import CrowAgent
from murder.agents.crow_handler import CrowHandler
from murder.agents.planning_agent import PlanningAgent
from murder.agents.planning_handler import PlanningHandler

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
