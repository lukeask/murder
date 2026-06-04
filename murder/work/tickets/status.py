"""Canonical TicketStatus enum — owned here; all other modules import from here."""

from __future__ import annotations

try:
    from enum import StrEnum
except ImportError:  # Python <3.11
    from enum import Enum

    class StrEnum(str, Enum):  # type: ignore[no-redef]
        def __str__(self) -> str:
            return str.__str__(self)


class TicketStatus(StrEnum):
    DRAFT = "draft"
    PLANNED = "planned"
    READY = "ready"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    DONE = "done"
    FAILED = "failed"
    ARCHIVED = "archived"
