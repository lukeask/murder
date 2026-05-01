"""The cast: Collaborator, Sentinel, Augur, Monkey.

See `.agents/1777410436NOTES.md` for role responsibilities and the
hierarchy: User → Collaborator → Sentinel → Augur → Monkey.
"""

from murder.agents.augur import AugurAgent
from murder.agents.base import Agent, AgentRole, AgentStatus
from murder.agents.collaborator import CollaboratorAgent
from murder.agents.monkey import MonkeyAgent
from murder.agents.sentinel import SentinelAgent

__all__ = [
    "Agent",
    "AgentRole",
    "AgentStatus",
    "AugurAgent",
    "CollaboratorAgent",
    "MonkeyAgent",
    "SentinelAgent",
]
