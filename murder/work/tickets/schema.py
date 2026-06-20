"""Ticket pydantic model — mirrors the SQLite tables (D2).

Tickets in v0 are split: metadata in DB, prose in `.murder/tickets/<id>.md`
This model is the in-memory aggregate view used by orchestrator and TUI.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

from murder.config import HarnessKind
from murder.work.tickets.status import TicketStatus


class ChecklistItem(BaseModel):
    id: int | None = None  # None until inserted
    ord: int
    text: str
    done: bool = False
    done_at: datetime | None = None


class Ticket(BaseModel):
    id: str  # 't007'
    title: str
    status: TicketStatus = TicketStatus.PLANNED
    deps: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    parent_id: str | None = None  # parent ticket id (subticket linkage); None = top-level
    harness: HarnessKind | None = None  # override default_crow
    model: str | None = None
    attempts: int = 0
    created_at: datetime
    updated_at: datetime
    checklist: list[ChecklistItem] = Field(default_factory=list)

    # Body sections (loaded from .murder/tickets/<id>.md)
    plan_body: str = ""
    working_notes: str = ""

    def md_path(self, agents_root: Path) -> Path:
        """Where this ticket's unified markdown (frontmatter + `# Checklist` body) lives (D9: flat)."""
        return agents_root / "tickets" / f"{self.id}.md"
