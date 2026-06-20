"""Typed records for hot persistence paths (W6)."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from murder.work.tickets.status import TicketStatus


@dataclass(frozen=True, slots=True)
class ChecklistItemRecord:
    id: int
    ord: int
    text: str
    done: bool
    done_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "ord": self.ord,
            "text": self.text,
            "done": self.done,
            "done_at": self.done_at,
        }

    def get(self, key: str, default: Any = None) -> Any:
        return self.to_dict().get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def __contains__(self, key: object) -> bool:
        return key in self.to_dict()

    def __iter__(self) -> Iterator[str]:
        return iter(self.to_dict())


@dataclass(frozen=True, slots=True)
class TicketRecord:
    id: str
    title: str
    status: TicketStatus
    harness: str | None
    model: str | None
    worktree: str | None
    attempts: int
    created_at: str
    updated_at: str
    deps: tuple[str, ...]
    skills: tuple[str, ...]
    checklist: tuple[ChecklistItemRecord, ...]
    last_error: str | None = None
    schedule_at: str | None = None
    parent_id: str | None = None
    metadata_hash: str | None = None
    metadata_file_hash: str | None = None
    metadata_last_materialized_hash: str | None = None
    metadata_materialized_path: str | None = None
    metadata_sync_state: str | None = None
    metadata_parse_error: str | None = None
    metadata_conflict_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        base: dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "status": self.status.value,
            "harness": self.harness,
            "model": self.model,
            "worktree": self.worktree,
            "attempts": self.attempts,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_error": self.last_error,
            "schedule_at": self.schedule_at,
            "parent_id": self.parent_id,
            "metadata_hash": self.metadata_hash,
            "metadata_file_hash": self.metadata_file_hash,
            "metadata_last_materialized_hash": self.metadata_last_materialized_hash,
            "metadata_materialized_path": self.metadata_materialized_path,
            "metadata_sync_state": self.metadata_sync_state,
            "metadata_parse_error": self.metadata_parse_error,
            "metadata_conflict_reason": self.metadata_conflict_reason,
            "deps": list(self.deps),
            "skills": list(self.skills),
            "checklist": [item.to_dict() for item in self.checklist],
        }
        return base

    def get(self, key: str, default: Any = None) -> Any:
        return self.to_dict().get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def __contains__(self, key: object) -> bool:
        return key in self.to_dict()

    def __iter__(self) -> Iterator[str]:
        return iter(self.to_dict())

    def keys(self) -> Iterator[str]:
        return iter(self.to_dict().keys())


@dataclass(frozen=True, slots=True)
class CommandRecord:
    id: str
    created_at: str
    updated_at: str
    run_id: str
    agent_id: str | None
    role: str | None
    ticket_id: str | None
    target_worker: str
    kind: str
    payload_json: str
    correlation_id: str
    idempotency_key: str
    status: str
    claimed_by: str | None
    lease_expires_at: int | None
    attempt_count: int
    retryable: int
    result_json: str | None
    last_error: str | None

    @property
    def payload(self) -> dict[str, Any]:
        return json.loads(self.payload_json or "{}")

    @property
    def result(self) -> dict[str, Any] | None:
        if not self.result_json:
            return None
        return json.loads(self.result_json)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "run_id": self.run_id,
            "agent_id": self.agent_id,
            "role": self.role,
            "ticket_id": self.ticket_id,
            "target_worker": self.target_worker,
            "kind": self.kind,
            "payload_json": self.payload_json,
            "correlation_id": self.correlation_id,
            "idempotency_key": self.idempotency_key,
            "status": self.status,
            "claimed_by": self.claimed_by,
            "lease_expires_at": self.lease_expires_at,
            "attempt_count": self.attempt_count,
            "retryable": self.retryable,
            "result_json": self.result_json,
            "last_error": self.last_error,
        }

    def get(self, key: str, default: Any = None) -> Any:
        return self.to_dict().get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def __contains__(self, key: object) -> bool:
        return key in self.to_dict()

    def __iter__(self) -> Iterator[str]:
        return iter(self.to_dict())


@dataclass(frozen=True, slots=True)
class EscalationRecord:
    id: int
    ts: str
    ticket_id: str | None
    severity: int
    reason: str
    to_recipient: str
    body_path: str | None
    resolved: bool
    resolved_at: str | None
    source_event_id: int | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "ts": self.ts,
            "ticket_id": self.ticket_id,
            "severity": self.severity,
            "reason": self.reason,
            "to_recipient": self.to_recipient,
            "body_path": self.body_path,
            "resolved": self.resolved,
            "resolved_at": self.resolved_at,
            "source_event_id": self.source_event_id,
        }

    def get(self, key: str, default: Any = None) -> Any:
        return self.to_dict().get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def __contains__(self, key: object) -> bool:
        return key in self.to_dict()

    def __iter__(self) -> Iterator[str]:
        return iter(self.to_dict())


def _row_get(row: Mapping[str, Any], key: str, default: Any = None) -> Any:
    """Read ``key`` from a sqlite3.Row or Mapping, tolerating absent columns.

    sqlite3.Row raises IndexError on a missing key (it has no ``.get``), so callers
    that build a row with a column subset stay safe for newer optional columns.
    """
    try:
        return row[key]
    except (KeyError, IndexError):
        return default


def ticket_record_from_row(
    row: Mapping[str, Any],
    *,
    deps: list[str],
    skills: list[str],
    checklist: list[ChecklistItemRecord],
) -> TicketRecord:
    return TicketRecord(
        id=str(row["id"]),
        title=str(row["title"]),
        status=TicketStatus(str(row["status"])),
        harness=row["harness"],
        model=row["model"],
        worktree=row["worktree"],
        attempts=int(row["attempts"] or 0),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        deps=tuple(deps),
        skills=tuple(skills),
        checklist=tuple(checklist),
        last_error=row["last_error"],
        schedule_at=row["schedule_at"],
        parent_id=_row_get(row, "parent_ticket_id"),
        metadata_hash=row["metadata_hash"],
        metadata_file_hash=row["metadata_file_hash"],
        metadata_last_materialized_hash=row["metadata_last_materialized_hash"],
        metadata_materialized_path=row["metadata_materialized_path"],
        metadata_sync_state=row["metadata_sync_state"],
        metadata_parse_error=row["metadata_parse_error"],
        metadata_conflict_reason=row["metadata_conflict_reason"],
    )


def command_record_from_row(row: Mapping[str, Any]) -> CommandRecord:
    return CommandRecord(
        id=str(row["id"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        run_id=str(row["run_id"]),
        agent_id=row["agent_id"],
        role=row["role"],
        ticket_id=row["ticket_id"],
        target_worker=str(row["target_worker"]),
        kind=str(row["kind"]),
        payload_json=str(row["payload_json"] or "{}"),
        correlation_id=str(row["correlation_id"]),
        idempotency_key=str(row["idempotency_key"]),
        status=str(row["status"]),
        claimed_by=row["claimed_by"],
        lease_expires_at=row["lease_expires_at"],
        attempt_count=int(row["attempt_count"] or 0),
        retryable=int(row["retryable"] or 0),
        result_json=row["result_json"],
        last_error=row["last_error"],
    )


def escalation_record_from_row(row: Mapping[str, Any]) -> EscalationRecord:
    return EscalationRecord(
        id=int(row["id"]),
        ts=str(row["ts"]),
        ticket_id=row["ticket_id"],
        severity=int(row["severity"]),
        reason=str(row["reason"]),
        to_recipient=str(row["to_recipient"]),
        body_path=row["body_path"],
        resolved=bool(row["resolved"]),
        resolved_at=row["resolved_at"],
        source_event_id=row["source_event_id"],
    )


__all__ = [
    "ChecklistItemRecord",
    "CommandRecord",
    "EscalationRecord",
    "TicketRecord",
    "command_record_from_row",
    "escalation_record_from_row",
    "ticket_record_from_row",
]
