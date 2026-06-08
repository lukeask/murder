from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from murder.bus.protocol import CommandEvent
from murder.runtime.workers.base import Worker, WorkerCtx, WorkerSpec

KickoffReady = Callable[[str | None], Awaitable[list[str]]]
ApplyCarveReady = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
CaptureSubmit = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
RetryFailed = Callable[[str], Awaitable[dict[str, Any]]]
SetScheduleAt = Callable[[str, str | None], Awaitable[dict[str, Any]]]
UpdateMetadata = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
ForceStatus = Callable[[str, str], Awaitable[dict[str, Any]]]
NoteEnsure = Callable[[str], Awaitable[dict[str, Any]]]
NoteRetire = Callable[[str], Awaitable[dict[str, Any]]]
SendAgentMessage = Callable[[str, str, str | None], Awaitable[dict[str, Any]]]
SendAgentKey = Callable[[str | None, str, bool, bool, str | None], Awaitable[dict[str, Any]]]
RefreshAgentTranscript = Callable[[str], Awaitable[dict[str, Any]]]
InterruptAgent = Callable[[str], Awaitable[dict[str, Any]]]
StopAgent = Callable[[str], Awaitable[dict[str, Any]]]
RenameRogue = Callable[[str, str], Awaitable[dict[str, Any]]]
ScaffoldPlan = Callable[[str, str], Awaitable[dict[str, Any]]]
RenamePlan = Callable[[str, str], Awaitable[dict[str, Any]]]
DeprecatePlan = Callable[[str], Awaitable[dict[str, Any]]]
QuickKickTicket = Callable[[str], Awaitable[dict[str, Any]]]
QuickCreateTicket = Callable[[str], dict[str, Any]]
SpawnRogue = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
ReconfigureCollaborator = Callable[[], Awaitable[dict[str, Any]]]


class OrchestratorCommandWorker(Worker):
    def __init__(
        self,
        *,
        kickoff_ready: KickoffReady,
        apply_carve_ready: ApplyCarveReady,
        capture_submit: CaptureSubmit,
        retry_failed: RetryFailed,
        set_schedule_at: SetScheduleAt,
        update_metadata: UpdateMetadata,
        force_status: ForceStatus,
        note_ensure: NoteEnsure,
        note_retire: NoteRetire,
        send_agent_message: SendAgentMessage,
        send_agent_key: SendAgentKey,
        refresh_agent_transcript: RefreshAgentTranscript,
        interrupt_agent: InterruptAgent,
        stop_agent: StopAgent,
        rename_rogue: RenameRogue,
        scaffold_plan: ScaffoldPlan,
        rename_plan: RenamePlan,
        deprecate_plan: DeprecatePlan,
        quick_kick_ticket: QuickKickTicket,
        quick_create_ticket: QuickCreateTicket,
        spawn_rogue: SpawnRogue,
        reconfigure_collaborator: ReconfigureCollaborator,
    ) -> None:
        super().__init__(
            WorkerSpec(
                name="orchestrator",
                process_model="thread",
                accepts=(
                    "scheduler.kickoff_ready",
                    "notetaker.capture.submit",
                    "note.ensure",
                    "note.retire",
                    "ticket.apply_carve_ready",
                    "ticket.retry_failed",
                    "ticket.set_schedule_at",
                    "ticket.update_metadata",
                    "ticket.force_status",
                    "agent.message",
                    "agent.send_key",
                    "agent.transcript.refresh",
                    "agent.interrupt",
                    "agent.stop",
                    "crow.rename_rogue",
                    "plan.scaffold",
                    "plan.rename",
                    "plan.deprecate",
                    "ticket.quick_kick",
                    "ticket.quick_create",
                    "crow.spawn_rogue",
                    "collaborator.reconfigure",
                ),
            )
        )
        self._kickoff_ready = kickoff_ready
        self._apply_carve_ready = apply_carve_ready
        self._capture_submit = capture_submit
        self._retry_failed = retry_failed
        self._set_schedule_at = set_schedule_at
        self._update_metadata = update_metadata
        self._force_status = force_status
        self._note_ensure = note_ensure
        self._note_retire = note_retire
        self._send_agent_message = send_agent_message
        self._send_agent_key = send_agent_key
        self._refresh_agent_transcript = refresh_agent_transcript
        self._interrupt_agent = interrupt_agent
        self._stop_agent = stop_agent
        self._rename_rogue = rename_rogue
        self._scaffold_plan = scaffold_plan
        self._rename_plan = rename_plan
        self._deprecate_plan = deprecate_plan
        self._quick_kick_ticket = quick_kick_ticket
        self._quick_create_ticket = quick_create_ticket
        self._spawn_rogue = spawn_rogue
        self._reconfigure_collaborator = reconfigure_collaborator

    async def run(self, ctx: WorkerCtx, stop_event: asyncio.Event) -> None:  # noqa: ARG002
        await stop_event.wait()

    async def on_command(self, command: CommandEvent, ctx: WorkerCtx) -> dict[str, Any]:  # noqa: ARG002
        if command.kind == "scheduler.kickoff_ready":
            only = command.payload.get("only")
            if only is not None and not isinstance(only, str):
                raise ValueError("scheduler.kickoff_ready payload.only must be a string or null")
            kicked = await self._kickoff_ready(only)
            return {"handled": True, "kicked": kicked}
        if command.kind == "ticket.apply_carve_ready":
            ticket_id = command.payload.get("ticket_id")
            if not isinstance(ticket_id, str) or not ticket_id.strip():
                raise ValueError("ticket.apply_carve_ready requires ticket_id")
            carve = command.payload.get("carve")
            if not (isinstance(carve, dict) and carve):
                raise ValueError(
                    "ticket.apply_carve_ready requires non-empty 'carve' in payload"
                )
            return await self._apply_carve_ready(ticket_id.strip(), command.payload)
        if command.kind == "notetaker.capture.submit":
            result = await self._capture_submit(dict(command.payload))
            return {"handled": True, **result}
        if command.kind == "note.ensure":
            name = command.payload.get("name")
            if not isinstance(name, str) or not name.strip():
                raise ValueError("note.ensure requires name")
            return await self._note_ensure(name.strip())
        if command.kind == "note.retire":
            name = command.payload.get("name")
            if not isinstance(name, str) or not name.strip():
                raise ValueError("note.retire requires name")
            return await self._note_retire(name.strip())
        if command.kind == "agent.message":
            agent_id = command.payload.get("agent_id")
            message = command.payload.get("message")
            if not isinstance(agent_id, str) or not agent_id.strip():
                raise ValueError("agent.message requires agent_id")
            if not isinstance(message, str):
                raise ValueError("agent.message requires message")
            ticket_id = command.payload.get("ticket_id")
            if ticket_id is not None and not isinstance(ticket_id, str):
                raise ValueError("agent.message ticket_id must be a string or null")
            return await self._send_agent_message(agent_id.strip(), message, ticket_id)
        if command.kind == "agent.send_key":
            raw_agent_id = command.payload.get("agent_id")
            if raw_agent_id is not None and (
                not isinstance(raw_agent_id, str) or not raw_agent_id.strip()
            ):
                raise ValueError("agent.send_key agent_id must be a string or null")
            key = command.payload.get("key")
            if not isinstance(key, str) or not key.strip():
                raise ValueError("agent.send_key requires key")
            literal = bool(command.payload.get("literal"))
            enter = bool(command.payload.get("enter"))
            log_user_input = command.payload.get("log_user_input")
            if log_user_input is not None and not isinstance(log_user_input, str):
                raise ValueError("agent.send_key log_user_input must be a string or null")
            agent_id = (
                raw_agent_id.strip()
                if isinstance(raw_agent_id, str) and raw_agent_id.strip()
                else None
            )
            return await self._send_agent_key(
                agent_id,
                key.strip(),
                literal=literal,
                enter=enter,
                log_user_input=log_user_input,
            )
        if command.kind == "agent.transcript.refresh":
            agent_id = command.payload.get("agent_id")
            if not isinstance(agent_id, str) or not agent_id.strip():
                raise ValueError("agent.transcript.refresh requires agent_id")
            return await self._refresh_agent_transcript(agent_id.strip())
        if command.kind == "agent.interrupt":
            agent_id = command.payload.get("agent_id")
            if not isinstance(agent_id, str) or not agent_id.strip():
                raise ValueError("agent.interrupt requires agent_id")
            return await self._interrupt_agent(agent_id.strip())
        if command.kind == "agent.stop":
            agent_id = command.payload.get("agent_id")
            if not isinstance(agent_id, str) or not agent_id.strip():
                raise ValueError("agent.stop requires agent_id")
            return await self._stop_agent(agent_id.strip())
        if command.kind == "crow.rename_rogue":
            agent_id = command.payload.get("agent_id")
            name = command.payload.get("name")
            if not isinstance(agent_id, str) or not agent_id.strip():
                raise ValueError("crow.rename_rogue requires agent_id")
            if not isinstance(name, str) or not name.strip():
                raise ValueError("crow.rename_rogue requires name")
            return await self._rename_rogue(agent_id.strip(), name.strip())
        if command.kind == "plan.scaffold":
            name = command.payload.get("name")
            body = command.payload.get("body")
            if not isinstance(name, str) or not name.strip():
                raise ValueError("plan.scaffold requires name")
            if not isinstance(body, str):
                raise ValueError("plan.scaffold requires body")
            return await self._scaffold_plan(name.strip(), body)
        if command.kind == "plan.rename":
            old_name = command.payload.get("old_name")
            new_name = command.payload.get("new_name")
            if not isinstance(old_name, str) or not old_name.strip():
                raise ValueError("plan.rename requires old_name")
            if not isinstance(new_name, str) or not new_name.strip():
                raise ValueError("plan.rename requires new_name")
            return await self._rename_plan(old_name.strip(), new_name.strip())
        if command.kind == "plan.deprecate":
            name = command.payload.get("name")
            if not isinstance(name, str) or not name.strip():
                raise ValueError("plan.deprecate requires name")
            return await self._deprecate_plan(name.strip())
        if command.kind == "ticket.retry_failed":
            ticket_id = command.payload.get("ticket_id")
            if not isinstance(ticket_id, str) or not ticket_id.strip():
                raise ValueError("ticket.retry_failed requires ticket_id")
            return await self._retry_failed(ticket_id.strip())
        if command.kind == "ticket.set_schedule_at":
            ticket_id = command.payload.get("ticket_id")
            schedule_at = command.payload.get("schedule_at")
            if not isinstance(ticket_id, str) or not ticket_id.strip():
                raise ValueError("ticket.set_schedule_at requires ticket_id")
            if schedule_at is not None and not isinstance(schedule_at, str):
                raise ValueError("ticket.set_schedule_at requires string or null schedule_at")
            return await self._set_schedule_at(ticket_id.strip(), schedule_at)
        if command.kind == "ticket.update_metadata":
            ticket_id = command.payload.get("ticket_id")
            if not isinstance(ticket_id, str) or not ticket_id.strip():
                raise ValueError("ticket.update_metadata requires ticket_id")
            return await self._update_metadata(ticket_id.strip(), dict(command.payload))
        if command.kind == "ticket.force_status":
            ticket_id = command.payload.get("ticket_id")
            status = command.payload.get("status")
            if not isinstance(ticket_id, str) or not ticket_id.strip():
                raise ValueError("ticket.force_status requires ticket_id")
            if not isinstance(status, str) or not status.strip():
                raise ValueError("ticket.force_status requires status")
            return await self._force_status(ticket_id.strip(), status.strip())
        if command.kind == "ticket.quick_kick":
            title = command.payload.get("title")
            if not isinstance(title, str) or not title.strip():
                raise ValueError("ticket.quick_kick requires title")
            return await self._quick_kick_ticket(title.strip())
        if command.kind == "ticket.quick_create":
            title = command.payload.get("title")
            if not isinstance(title, str) or not title.strip():
                raise ValueError("ticket.quick_create requires title")
            return self._quick_create_ticket(title.strip())
        if command.kind == "crow.spawn_rogue":
            return await self._spawn_rogue(dict(command.payload))
        if command.kind == "collaborator.reconfigure":
            return await self._reconfigure_collaborator()
        return {"handled": False}
