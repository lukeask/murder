"""The cast: Collaborator, Notetaker, Sentinel, CrowHandler, Crow.

See `.murder/1777410436NOTES.md` for role responsibilities and the
hierarchy: User → Collaborator → Sentinel → CrowHandler → Crow. The
Notetaker is an alternate planning-mode partner (see `agents/notetaker.py`).
"""

from murder.agents.base import Agent, AgentRole, AgentStatus
from murder.agents.collaborator import CollaboratorAgent
from murder.agents.crow import CrowAgent
from murder.agents.crow_handler import CrowHandlerAgent
from murder.agents.notetaker import NotetakerAgent
from murder.agents.sentinel import SentinelAgent

__all__ = [
    "Agent",
    "AgentRole",
    "AgentStatus",
    "CollaboratorAgent",
    "CrowAgent",
    "CrowHandlerAgent",
    "NotetakerAgent",
    "SentinelAgent",
]
