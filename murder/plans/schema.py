"""Plan pydantic model.

Plans store prose; frontmatter carries the small amount of structured
state that the runtime syncs into SQLite. `.agents/plans/<name>.md` is the
editable working projection, not a separate source of truth.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

try:
    from enum import StrEnum
except ImportError:  # Python <3.11
    class StrEnum(str, Enum):
        def __str__(self) -> str:
            return str.__str__(self)

from pydantic import BaseModel, Field


class PlanStatus(StrEnum):
    DRAFT = "draft"
    ACCEPTED = "accepted"
    SUPERSEDED = "superseded"


class Plan(BaseModel):
    name: str
    status: PlanStatus = PlanStatus.DRAFT
    created_at: datetime
    updated_at: datetime | None = None
    revisions: int = 0
    related_tickets: list[str] = Field(default_factory=list)
    frontmatter: dict[str, object] = Field(default_factory=dict)
    body: str = ""
