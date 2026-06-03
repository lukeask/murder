"""Semantic escalation operations (W5)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from murder.agents.base import AgentRole
from murder.bus.protocol import EscalationEvent, TicketStatus

from murder.persistence.records import EscalationRecord

from ..persistence import escalations as dbmod
from ..storage.filesystem import atomic_write_text
from ..storage.paths import escalation_md

if TYPE_CHECKING:
    from murder.bus.broker import Bus


def _clamp_severity(severity: int) -> int:
    return max(1, min(3, severity))


@dataclass(slots=True)
class EscalationService:
    """Insert escalation rows, optional markdown bodies, and bus events."""

    conn: sqlite3.Connection
    repo_root: Path
    bus: Bus | None = None
    run_id: str | None = None
    agent_id: str = "orchestrator"
    role: AgentRole = AgentRole.COLLABORATOR

    async def escalate_to_user(
        self,
        reason: str,
        *,
        severity: int = 2,
        ticket_id: str | None = None,
        source_event_id: int | None = None,
    ) -> int:
        sev = _clamp_severity(severity)
        eid = dbmod.insert_escalation(
            self.conn,
            ticket_id=ticket_id,
            severity=sev,
            reason=reason,
            to_recipient="user",
            source_event_id=source_event_id,
        )
        await self._publish(ticket_id=ticket_id, reason=reason, severity=sev, to="user")
        return eid

    async def escalate_to_collaborator(
        self,
        reason: str,
        body: str,
        *,
        ticket_id: str | None = None,
        severity: int = 2,
    ) -> tuple[int, Path]:
        sev = _clamp_severity(severity)
        eid = dbmod.insert_escalation(
            self.conn,
            ticket_id=ticket_id,
            severity=sev,
            reason=reason,
            to_recipient="collaborator",
        )
        path = escalation_md(self.repo_root, eid)
        atomic_write_text(path, body)
        self.conn.execute(
            "UPDATE escalations SET body_path = ? WHERE id = ?",
            (str(path), eid),
        )
        await self._publish(ticket_id=ticket_id, reason=reason, severity=sev, to="collaborator")
        return eid, path

    async def record_ticket_failure(self, ticket_id: str, reason: str) -> int:
        """User escalation after ticket is already failed (lifecycle stays in orchestrator)."""
        return await self.escalate_to_user(reason, severity=2, ticket_id=ticket_id)

    async def record_kickoff_conflict(self, reason: str) -> int:
        return await self.escalate_to_user(reason, severity=2, ticket_id=None)

    async def record_collaborator_startup_failure(self, reason: str) -> int:
        return await self.escalate_to_user(reason, severity=2, ticket_id=None)

    def list_active(self, recipient: str | None = None) -> list[EscalationRecord]:
        return dbmod.list_pending_escalations(self.conn, recipient)

    def resolve(self, escalation_id: int) -> None:
        dbmod.resolve_escalation(self.conn, escalation_id)

    async def _publish(
        self,
        *,
        ticket_id: str | None,
        reason: str,
        severity: int,
        to: str,
    ) -> None:
        if self.bus is None or self.run_id is None:
            return
        await self.bus.publish(
            EscalationEvent(
                run_id=self.run_id,
                agent_id=self.agent_id,
                role=self.role,
                ticket_id=ticket_id,
                to=to,  # type: ignore[arg-type]
                reason=reason,
                severity=severity,  # type: ignore[arg-type]
            )
        )
