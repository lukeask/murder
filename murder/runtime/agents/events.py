"""Typed agent events and the sink interface that consumes them."""

from __future__ import annotations

import logging
from dataclasses import dataclass, fields
from datetime import datetime
from typing import Protocol, TypeAlias

from murder.observability.advanced_log import current_advanced_log

log = logging.getLogger(__name__)

# LogRecord attributes (and our formatter's core keys) we must never shadow by
# passing a same-named key through ``extra=``. ``logging`` raises if an extra key
# collides with a reserved record attribute, so we remap any such field name.
_RESERVED_EXTRA_KEYS: frozenset[str] = frozenset(
    {
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread", "threadName",
        "processName", "process", "taskName", "message", "asctime",
    }
)

# Per-event log level. Failures warn; everything else is INFO default-tier.
_EVENT_LEVELS: dict[str, int] = {
    "AgentFailedEvent": logging.WARNING,
}


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
        event_type = type(event).__name__
        extra: dict[str, object] = {"event_type": event_type}
        # Flight-recorder payload mirrors the structured event fields but keeps
        # original field names (the recorder has no reserved-key constraint).
        record_payload: dict[str, object] = {"event_type": event_type}
        for field in fields(event):
            key = field.name
            value = getattr(event, key)
            record_payload[key] = value
            # ``session_name`` already rides in the message string; keep it
            # structured too. Remap any field whose name would collide with a
            # reserved LogRecord attribute (e.g. ``message``) before it reaches
            # ``extra=``, which would otherwise raise.
            if key in _RESERVED_EXTRA_KEYS:
                key = f"event_{key}"
            extra[key] = value
        # Phase 2 flight recorder (no-op when advanced logging is off).
        current_advanced_log().record_agent(payload=record_payload)
        level = _EVENT_LEVELS.get(event_type, logging.INFO)
        self._logger.log(
            level,
            "agent event %s session_name=%s",
            event_type,
            event.session_name,
            extra=extra,
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
