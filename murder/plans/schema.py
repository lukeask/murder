"""Plan pydantic model.

Plans store prose; only the frontmatter is structured. They live in
`.agents/plans/<name>.md`. No DB row in v0 — plans aren't read by the
runtime, only by user + Collaborator.
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
    revisions: int = 0
    related_tickets: list[str] = Field(default_factory=list)
    body: str = ""
