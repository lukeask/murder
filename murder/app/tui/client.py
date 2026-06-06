from __future__ import annotations

import asyncio
import json
import shlex
import subprocess
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import uuid4

from murder.app.service.client_api import (
    CommandRequest,
    CommandResult,
    CommandStatus,
    ConversationsSnapshot,
    CrowSnapshot,
    DispatchSnapshot,
    EscalationsSnapshot,
    NoteDisplaySnapshot,
    NotesSnapshot,
    PlanDisplaySnapshot,
    PlansSnapshot,
    ReportDisplaySnapshot,
    ReportsSnapshot,
    ScheduleSnapshot,
    TicketCarveSnapshot,
    TicketDetailSnapshot,
    UsageGaugeDrillInSnapshot,
    dto_from_wire,
)
from murder.app.tui.pane_capture import PaneCaptureError
from murder.bus.client import SocketBusClient
from murder.bus.protocol import BusEvent, ClientKind, EventFilter
from murder.config import Config

COMMAND_POLL_S = 0.05
STATE_RPC_TIMEOUT_S = 10.0


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
        self.run_id: str | None = None
        self.note_sync = None

    async def connect(self) -> None:
        reply = await self.bus.request("health.ping", {}, timeout_s=5.0)
        self.run_id = str(reply.get("run_id") or "")

    async def close(self) -> None:
        return None

    async def reconcile_plan(self, name: str) -> None:
        await self.bus.request(
            "document.reconcile_plan",
            {"name": name},
            timeout_s=STATE_RPC_TIMEOUT_S,
        )

    async def plan_path_for(self, name: str) -> Path:
        return Path(
            await self._request_value(
                "document.plan_path",
                {"name": name},
                str,
            )
        )

    async def note_path_for(self, name: str) -> Path:
        return Path(
            await self._request_value(
                "document.note_path",
                {"name": name},
                str,
            )
        )

    async def report_path_for(self, name: str) -> Path:
        return Path(
            await self._request_value(
                "document.report_path",
                {"name": name},
                str,
            )
        )

    def open_editor_blocking(self, path: Path, preferred_editor: str | None = None) -> int:
        from murder.work.plans.sync import choose_editor

        editor = choose_editor(preferred_editor)
        argv = shlex.split(editor) or ["vi"]
        proc = subprocess.run([*argv, str(path)], check=False)
        return int(proc.returncode)

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
        return await self._request_value("state.dispatch_snapshot", {}, DispatchSnapshot)

    async def get_schedule_snapshot(self) -> ScheduleSnapshot:
        return await self._request_value("state.schedule_snapshot", {}, ScheduleSnapshot)

    async def get_ticket_detail(self, ticket_id: str) -> TicketDetailSnapshot | None:
        return await self._request_optional(
            "state.ticket_detail",
            {"ticket_id": ticket_id},
            TicketDetailSnapshot,
        )

    async def get_crow_snapshot(self) -> CrowSnapshot:
        return await self._request_value("state.crow_snapshot", {}, CrowSnapshot)

    async def get_conversations_snapshot(self) -> ConversationsSnapshot:
        return await self._request_value(
            "state.conversations_snapshot",
            {},
            ConversationsSnapshot,
        )

    async def subscribe_conversation_blocks(self) -> AsyncIterator[BusEvent]:
        async for event in self.bus.subscribe(EventFilter(type="conversation.block")):
            yield event

    async def get_escalations(self) -> EscalationsSnapshot:
        return await self._request_value("state.escalations_snapshot", {}, EscalationsSnapshot)

    async def ack_escalation(self, escalation_id: int) -> None:
        await self.submit_command(
            target_worker="state",
            kind="state.escalation.ack",
            payload={"escalation_id": escalation_id},
            timeout_s=10.0,
        )

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

    async def interrupt_agent(self, agent_id: str) -> CommandResult:
        return await self._submit_raw(
            target_worker="orchestrator",
            kind="agent.interrupt",
            payload={"agent_id": agent_id},
            timeout_s=15.0,
        )

    async def spawn_rogue(
        self,
        harness: str,
        model: str,
        effort: str | None = None,
        name: str | None = None,
        *,
        worktree_path: str | None = None,
        worktree_branch: str | None = None,
    ) -> str:
        payload: dict[str, object] = {"harness": harness, "model": model}
        if effort is not None:
            payload["effort"] = effort
        if name is not None:
            payload["name"] = name
        if worktree_path is not None:
            payload["worktree_path"] = worktree_path
        if worktree_branch is not None:
            payload["worktree_branch"] = worktree_branch
        result = await self.submit_command(
            target_worker="orchestrator",
            kind="crow.spawn_rogue",
            payload=payload,
            timeout_s=120.0,
        )
        agent_id = str(result.get("agent_id") or "")
        if not agent_id:
            raise RuntimeError("crow.spawn_rogue did not return agent_id")
        return agent_id

    async def capture_pane(self, session: str, *, lines: int = 200) -> str:
        reply = await self.bus.request(
            "tmux.capture_pane",
            {"session": session, "lines": int(lines)},
            timeout_s=STATE_RPC_TIMEOUT_S,
        )
        if not reply.get("ok"):
            raise PaneCaptureError(str(reply.get("error") or "tmux.capture_pane failed"))
        return str(reply.get("text") or "")

    async def run_shell_command(
        self, command: str, *, prior_session: str | None = None
    ) -> str:
        body: dict[str, object] = {"command": command}
        if prior_session:
            body["prior_session"] = prior_session
        reply = await self.bus.request("tmux.shell_run", body, timeout_s=STATE_RPC_TIMEOUT_S)
        if not reply.get("ok"):
            raise RuntimeError(str(reply.get("error") or "tmux.shell_run failed"))
        session_name = str(reply.get("session_name") or "")
        if not session_name:
            raise RuntimeError("tmux.shell_run did not return session_name")
        return session_name

    async def get_plans_snapshot(self) -> PlansSnapshot:
        return await self._request_value("state.plans_snapshot", {}, PlansSnapshot)

    async def get_notes_snapshot(self) -> NotesSnapshot:
        return await self._request_value("state.notes_snapshot", {}, NotesSnapshot)

    async def get_reports_snapshot(self) -> ReportsSnapshot:
        return await self._request_value("state.reports_snapshot", {}, ReportsSnapshot)

    async def get_plan_display(self, name: str) -> PlanDisplaySnapshot | None:
        return await self._request_optional(
            "state.plan_display",
            {"name": name},
            PlanDisplaySnapshot,
        )

    async def get_note_display(self, name: str) -> NoteDisplaySnapshot | None:
        return await self._request_optional(
            "state.note_display",
            {"name": name},
            NoteDisplaySnapshot,
        )

    async def get_report_display(self, name: str) -> ReportDisplaySnapshot | None:
        return await self._request_optional(
            "state.report_display",
            {"name": name},
            ReportDisplaySnapshot,
        )

    async def get_usage_gauge_drill_in(
        self,
        *,
        harness: str,
        window_key: str,
        t_period_minutes: float,
    ) -> UsageGaugeDrillInSnapshot:
        return await self._request_value(
            "state.usage_gauge_drill_in",
            {
                "harness": harness,
                "window_key": window_key,
                "t_period_minutes": t_period_minutes,
            },
            UsageGaugeDrillInSnapshot,
        )

    async def get_ticket_carve_snapshot(
        self,
        ticket_id: str,
    ) -> TicketCarveSnapshot | None:
        return await self._request_optional(
            "state.ticket_carve",
            {"ticket_id": ticket_id},
            TicketCarveSnapshot,
        )

    async def get_ticket_status(self, ticket_id: str) -> str | None:
        return await self._request_optional(
            "state.ticket_status",
            {"ticket_id": ticket_id},
            str,
        )

    async def get_notetaker_recent_entries(self, limit: int = 50) -> list[dict[str, object]]:
        rows = await self._request_value(
            "state.notetaker_recent_entries",
            {"limit": limit},
            list,
        )
        return [dict(row) for row in rows]

    async def _request_value(
        self,
        target: str,
        body: dict[str, object],
        cls: type[Any],
    ) -> Any:
        reply = await self.bus.request(target, body, timeout_s=STATE_RPC_TIMEOUT_S)
        return dto_from_wire(cls, reply.get("value"))

    async def _request_optional(
        self,
        target: str,
        body: dict[str, object],
        cls: type[Any],
    ) -> Any | None:
        reply = await self.bus.request(target, body, timeout_s=STATE_RPC_TIMEOUT_S)
        value = reply.get("value")
        if value is None:
            return None
        return dto_from_wire(cls, value)
