"""Typed agent events and the sink interface that consumes them."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, TypeAlias

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AgentStartedEvent:
    session_name: str
    started_at: datetime


@dataclass(frozen=True, slots=True)
class AgentMessageEvent:
    session_name: str
    message: str
    timestamp: datetime


@dataclass(frozen=True, slots=True)
class AgentBlockedEvent:
    session_name: str
    reason: str
    timestamp: datetime


@dataclass(frozen=True, slots=True)
class AgentDoneEvent:
    session_name: str
    outcome: str
    timestamp: datetime


@dataclass(frozen=True, slots=True)
class AgentFailedEvent:
    session_name: str
    error: str
    timestamp: datetime


@dataclass(frozen=True, slots=True)
class AgentNeedsDecisionEvent:
    session_name: str
    question: str
    choices: list[str]
    timestamp: datetime


AgentEvent: TypeAlias = (
    AgentStartedEvent
    | AgentMessageEvent
    | AgentBlockedEvent
    | AgentDoneEvent
    | AgentFailedEvent
    | AgentNeedsDecisionEvent
)


class AgentEventSink(Protocol):
    async def emit(self, event: AgentEvent) -> None:
        """Consume one agent event."""


class LoggingAgentEventSink:
    """Default logging sink before real subscribers exist."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or log

    async def emit(self, event: AgentEvent) -> None:
        self._logger.info(
            "agent event %s session_name=%s",
            type(event).__name__,
            event.session_name,
        )


__all__ = [
    "AgentBlockedEvent",
    "AgentDoneEvent",
    "AgentEvent",
    "AgentEventSink",
    "AgentFailedEvent",
    "AgentMessageEvent",
    "AgentNeedsDecisionEvent",
    "AgentStartedEvent",
    "LoggingAgentEventSink",
]
