"""Command claim/execute/complete/fail/reap (W3)."""

from __future__ import annotations

import json
import logging
import math
import sqlite3
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

from murder.runtime.agents.types import AgentRole
from murder.runtime.orchestration.events import (
    COMMAND_REAPER_INTERVAL_S,
    DEFAULT_LEASE_TTL_S,
    DEFAULT_MAX_COMMAND_ATTEMPTS,
    CommandEvent,
    CommandStatus,
)
from murder.observability.advanced_log import CommandRecord, current_advanced_log
from murder.observability.log_context import log_context
from murder.runtime.orchestration.commands import OrchestrationCommand
from murder.runtime.orchestration.worker_names import WorkerName
from murder.state.persistence import commands as cmd_db
from murder.state.persistence.records import CommandRecord as PersistedCommandRecord
from murder.verdict.escalations.service import EscalationService

if TYPE_CHECKING:
    from murder.runtime.orchestration.notifier import OrchestrationNotifier

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class ClaimedCommand:
    command_id: str
    event: CommandEvent


@dataclass
class CommandDispatcher:
    conn: sqlite3.Connection
    repo_root: Path
    bus: OrchestrationNotifier | None = None
    lease_ttl_s: float = DEFAULT_LEASE_TTL_S
    max_attempts: int = DEFAULT_MAX_COMMAND_ATTEMPTS
    reaper_interval_s: float = COMMAND_REAPER_INTERVAL_S

    def claim_next(
        self, *, target_worker: WorkerName, claimed_by: str
    ) -> ClaimedCommand | None:
        lease_expires_at = math.ceil(time.time() + self.lease_ttl_s)
        row = cmd_db.claim_next_command(
            self.conn,
            target_worker=target_worker,
            claimed_by=claimed_by,
            lease_expires_at=lease_expires_at,
        )
        if row is None:
            return None
        row_id = str(row["id"])
        with log_context(command_id=row_id):
            try:
                event = command_from_row(row)
            except ValueError as exc:
                # Quarantine a corrupt row (non-UUID id) so it leaves the pending
                # set instead of wedging the claim loop. This indicates corruption
                # or an out-of-band write, so fail loudly and move on.
                LOGGER.error("dropping corrupt command row: %s", exc)
                self.fail(row_id, "non-UUID command id", retryable=False)
                return None
            current_advanced_log().record_command(
                CommandRecord(
                    phase="claim",
                    command_id=row_id,
                    command=event.model_dump(mode="json"),
                )
            )
            return ClaimedCommand(
                command_id=row_id,
                event=event,
            )

    def complete(self, command_id: str, result: dict[str, Any] | None) -> None:
        with log_context(command_id=command_id):
            current_advanced_log().record_command(
                CommandRecord(phase="complete", command_id=command_id, result=result)
            )
            cmd_db.complete_command(self.conn, command_id=command_id, result=result)

    def renew(self, command_id: str, *, claimed_by: str) -> bool:
        """Keep an actively executing command from becoming re-dispatchable."""

        lease_expires_at = math.ceil(time.time() + self.lease_ttl_s)
        return cmd_db.renew_command_lease(
            self.conn,
            command_id=command_id,
            claimed_by=claimed_by,
            lease_expires_at=lease_expires_at,
        )

    def fail(self, command_id: str, last_error: str, *, retryable: bool = True) -> None:
        with log_context(command_id=command_id):
            current_advanced_log().record_command(
                CommandRecord(
                    phase="fail",
                    command_id=command_id,
                    last_error=last_error,
                    retryable=retryable,
                )
            )
            cmd_db.fail_command(
                self.conn,
                command_id=command_id,
                last_error=last_error,
                retryable=retryable,
            )

    def finish(
        self,
        *,
        command_id: str,
        command: CommandEvent,
        worker_name: str,
        result: dict[str, Any],
    ) -> None:
        if result.get("handled") is False:
            # Wiring miss: a command was routed to a worker that has no branch
            # for this kind. That is a programming bug, not a runtime condition,
            # so fail loudly at ERROR level.
            message = f"worker {worker_name!r} did not handle {command.kind.value!r}"
            LOGGER.error(message)
            self.fail(command_id, message, retryable=False)
            return
        if result.get("ok") is False:
            # Domain failure: the handler ran fine and hit a normal business
            # error (e.g. "no agent named X"). Surface the handler's own error.
            error = result.get("error")
            message = str(error) if error else f"command {command.kind.value!r} failed"
            self.fail(command_id, message, retryable=False)
            return
        self.complete(command_id, result)

    def reap_stale(self) -> dict[str, list[str]]:
        return cmd_db.reap_stale_commands(
            self.conn,
            now_epoch=int(time.time()),
            max_attempts=self.max_attempts,
        )

    async def escalate_retry_exhaustion(self, command_ids: list[str]) -> None:
        if not command_ids:
            return
        for command_id in command_ids:
            row = self.conn.execute(
                "SELECT * FROM commands WHERE id = ?",
                (command_id,),
            ).fetchone()
            if row is None:
                continue
            reason = (
                f"Command {command_id} for worker {row['target_worker']} "
                f"failed after retry exhaustion: {row['last_error'] or 'unknown error'}"
            )
            svc = EscalationService(
                conn=self.conn,
                repo_root=self.repo_root,
                bus=self.bus,
                run_id=str(row["run_id"]),
                agent_id="supervisor",
            )
            await svc.escalate_to_user(
                reason,
                severity=2,
                ticket_id=row["ticket_id"],
            )


def command_from_row(row: PersistedCommandRecord | Mapping[str, Any]) -> CommandEvent:
    role = row.get("role")
    status = row.get("status") or CommandStatus.PENDING.value
    try:
        event_id = UUID(str(row["id"]))
    except ValueError as exc:
        raise ValueError(f"commands row has non-UUID id: {row['id']!r}") from exc
    return CommandEvent(
        id=event_id,
        run_id=row["run_id"],
        agent_id=row.get("agent_id") or "",
        role=AgentRole(role) if role else None,
        ticket_id=row.get("ticket_id"),
        target_worker=WorkerName(row["target_worker"]),
        kind=OrchestrationCommand(row["kind"]),
        payload=json.loads(row.get("payload_json") or "{}"),
        correlation_id=row["correlation_id"],
        idempotency_key=row["idempotency_key"],
        status=CommandStatus(status),
        claimed_by=row.get("claimed_by"),
        lease_expires_at=row.get("lease_expires_at"),
        attempt_count=int(row.get("attempt_count") or 0),
        retryable=bool(row.get("retryable")),
        result=json.loads(row["result_json"]) if row.get("result_json") else None,
    )
