"""Closed identifiers for private orchestration workers.

These names route durable commands between the scheduler, supervisor, and
workers. They deliberately live below the worker implementations so the bus
contract can depend on them without importing worker runtime code.
"""

from __future__ import annotations

from enum import Enum


class WorkerName(str, Enum):
    COLLABORATOR = "collaborator"
    CODEBASE_MAP = "codebase-map"
    DONE_SESSION_SWEEPER = "done-session-sweeper"
    HARNESS_VERSION_PROBE = "harness-version-probe"
    NOTE_SYNC = "note_sync"
    ORCHESTRATOR = "orchestrator"
    PLAN_SYNC = "plan_sync"
    PLANNER_SESSION_SWEEPER = "planner-session-sweeper"
    SCHEDULER = "scheduler"
    STATE = "state"
    USAGE_PROBE = "usage-probe"

    def __str__(self) -> str:
        return str.__str__(self)


__all__ = ["WorkerName"]
