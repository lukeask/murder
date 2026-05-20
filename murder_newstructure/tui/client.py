from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from uuid import uuid4

from murder_newstructure.bus.client import SocketBusClient
from murder_newstructure.bus.protocol import ClientKind
from murder_newstructure.config import Config
from murder_newstructure.persistence.schema import get_db
from murder_newstructure.service.client_api import (
    CommandRequest,
    CommandResult,
    CommandStatus,
    CrowSnapshot,
    DispatchSnapshot,
    EscalationsSnapshot,
    MurderServiceClient,
    NotesSnapshot,
    PlansSnapshot,
    ScheduleSnapshot,
    TicketDetailSnapshot,
)
from murder_newstructure.service.document_access import DocumentAccess
from murder_newstructure.service.read_model import ServiceReadModel
from murder_newstructure.storage.paths import db_path

COMMAND_POLL_S = 0.05


class TuiRuntimeClient:
    """TUI-facing service facade implementing :class:`MurderServiceClient`."""

    def __init__(
        self,
        repo_root: Path,
        socket_path: Path,
        config: Config,
        *,
        client_kind: ClientKind = ClientKind.TUI,
    ) -> None:
        self.repo_root = repo_root
        self.config = config
        self.bus = SocketBusClient(
            socket_path,
            client_kind=client_kind,
            client_id=f"{client_kind.value}-{uuid4().hex}",
        )
        self._db = get_db(db_path(repo_root))
        self.read_model = ServiceReadModel(db_path(repo_root))
        self.documents = DocumentAccess(repo_root, self._db)
        self.run_id: str | None = None
        self.note_sync = None

    @property
    def db(self):
        """Transitional: note capture paths still mutate via SQLite."""
        return self._db

    async def connect(self) -> None:
        reply = await self.bus.request("health.ping", {}, timeout_s=5.0)
        self.run_id = str(reply.get("run_id") or "")

    async def close(self) -> None:
        self._db.close()

    async def reconcile_plan(self, name: str) -> None:
        await self.documents.reconcile_plan(name)

    def plan_path_for(self, name: str) -> Path:
        return self.documents.plan_path_for(name)

    def note_path_for(self, name: str) -> Path:
        return self.documents.note_path_for(name)

    def open_editor_blocking(self, path: Path, preferred_editor: str | None = None) -> int:
        return self.documents.open_editor_blocking(path, preferred_editor)

    async def submit_command(
        self,
        *,
        target_worker: str,
        kind: str,
        payload: dict[str, object],
        timeout_s: float,
    ) -> dict[str, object]:
        """Submit a durable command and poll until done or failed."""
        result = await self._submit_raw(
            target_worker=target_worker,
            kind=kind,
            payload=payload,
            timeout_s=timeout_s,
        )
        if result.status == CommandStatus.FAILED:
            raise RuntimeError(result.error or f"{kind} failed")
        return dict(result.result or {})

    async def submit_command_typed(self, request: CommandRequest) -> CommandResult:
        payload = dict(request.payload)
        timeout_s = float(payload.pop("timeout_s", 30.0))
        target_worker = str(payload.pop("target_worker", ""))
        return await self._submit_raw(
            target_worker=target_worker,
            kind=request.command_type,
            payload=payload,
            timeout_s=timeout_s,
            correlation_id=request.correlation_id,
            idempotency_key=request.idempotency_key,
        )

    async def _submit_raw(
        self,
        *,
        target_worker: str,
        kind: str,
        payload: dict[str, object],
        timeout_s: float,
        correlation_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> CommandResult:
        submitted = await self.bus.request(
            "command.submit",
            {
                "target_worker": target_worker,
                "kind": kind,
                "payload": payload,
                "agent_id": "tui",
                "correlation_id": correlation_id or f"tui-{uuid4()}",
                "idempotency_key": idempotency_key or f"tui-{kind}-{uuid4()}",
            },
            timeout_s=min(timeout_s, 10.0),
        )
        command_id = str(submitted.get("command_id") or "")
        if not command_id:
            raise RuntimeError("command.submit did not return command_id")
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            status = await self.bus.request(
                "command.status",
                {"command_id": command_id},
                timeout_s=min(timeout_s, 10.0),
            )
            state = str(status.get("status") or "")
            if state == "done":
                raw = status.get("result_json")
                parsed = json.loads(str(raw)) if raw else {}
                return CommandResult(
                    command_id=command_id,
                    status=CommandStatus.ACCEPTED,
                    result=parsed,
                )
            if state == "failed":
                return CommandResult(
                    command_id=command_id,
                    status=CommandStatus.FAILED,
                    error=str(status.get("last_error") or f"{kind} failed"),
                )
            await asyncio.sleep(COMMAND_POLL_S)
        raise TimeoutError(f"{kind} timed out")

    async def get_dispatch_snapshot(self) -> DispatchSnapshot:
        return self.read_model.get_dispatch_snapshot()

    async def get_schedule_snapshot(self) -> ScheduleSnapshot:
        return self.read_model.get_schedule_snapshot()

    async def get_ticket_detail(self, ticket_id: str) -> TicketDetailSnapshot | None:
        try:
            return self.read_model.get_ticket_detail(ticket_id)
        except KeyError:
            return None

    async def get_crow_snapshot(self) -> CrowSnapshot:
        return self.read_model.get_crow_snapshot()

    async def get_escalations(self) -> EscalationsSnapshot:
        return self.read_model.get_escalations_snapshot()

    async def ack_escalation(self, escalation_id: int) -> None:
        self.read_model.ack_escalation(str(escalation_id))

    async def send_agent_message(
        self,
        agent_id: str,
        message: str,
        *,
        ticket_id: str | None = None,
    ) -> CommandResult:
        payload: dict[str, object] = {"agent_id": agent_id, "message": message}
        if ticket_id is not None:
            payload["ticket_id"] = ticket_id
        return await self._submit_raw(
            target_worker="orchestrator",
            kind="agent.message",
            payload=payload,
            timeout_s=30.0,
        )

    async def get_plans_snapshot(self) -> PlansSnapshot:
        return self.read_model.get_plans_snapshot()

    async def get_notes_snapshot(self) -> NotesSnapshot:
        return self.read_model.get_notes_snapshot()
