from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from murder.bus.protocol import CommandEvent
from murder.runtime.workers.base import Worker, WorkerCtx, WorkerSpec


class OrchestratorCommands(Protocol):
    """Exactly the orchestrator surface the command worker dispatches to.

    Capability-narrowing: the worker depends on this Protocol, not the whole
    ``Orchestrator``. ``Orchestrator`` satisfies it structurally.
    """

    async def kickoff_ready(self, only: str | None) -> list[str]: ...

    async def apply_ticket_carve_ready(
        self, ticket_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]: ...

    async def submit_notetaker_capture(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    async def retry_failed_ticket(self, ticket_id: str) -> dict[str, Any]: ...

    async def reset_crow(self, ticket_id: str) -> dict[str, Any]: ...

    async def set_schedule_at(
        self, ticket_id: str, schedule_at: str | None
    ) -> dict[str, Any]: ...

    async def update_ticket_metadata(
        self, ticket_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]: ...

    async def force_ticket_status(self, ticket_id: str, status: str) -> dict[str, Any]: ...

    async def ensure_note(self, name: str) -> dict[str, Any]: ...

    async def retire_note(self, name: str) -> dict[str, Any]: ...

    async def dismiss_history_item(self, item_id: str) -> dict[str, Any]: ...

    async def resume_conversation(self, conversation_id: str) -> dict[str, Any]: ...

    async def send_agent_message(
        self, agent_id: str, message: str, ticket_id: str | None
    ) -> dict[str, Any]: ...

    async def send_agent_key(
        self,
        agent_id: str | None,
        key: str,
        *,
        literal: bool = False,
        enter: bool = False,
        log_user_input: str | None = None,
    ) -> dict[str, Any]: ...

    async def refresh_agent_transcript(self, agent_id: str) -> dict[str, Any]: ...

    async def interrupt_agent(self, agent_id: str) -> dict[str, Any]: ...

    async def stop_agent(self, agent_id: str) -> dict[str, Any]: ...

    async def rename_rogue_agent(self, agent_id: str, name: str) -> dict[str, Any]: ...

    async def scaffold_plan(self, name: str, body: str) -> dict[str, Any]: ...

    async def rename_plan(self, old_name: str, new_name: str) -> dict[str, Any]: ...

    async def deprecate_plan(self, name: str) -> dict[str, Any]: ...

    async def quick_kick_ticket(self, title: str) -> dict[str, Any]: ...

    def quick_create_ticket(self, title: str) -> dict[str, Any]: ...

    async def spawn_rogue_command(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    async def spawn_planner_command(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    async def reconfigure_collaborator(self) -> dict[str, Any]: ...


Handler = Callable[[OrchestratorCommands, dict[str, Any]], Awaitable[dict[str, Any]]]


async def _kickoff_ready(orch: OrchestratorCommands, payload: dict[str, Any]) -> dict[str, Any]:
    only = payload.get("only")
    if only is not None and not isinstance(only, str):
        raise ValueError("scheduler.kickoff_ready payload.only must be a string or null")
    kicked = await orch.kickoff_ready(only)
    return {"handled": True, "kicked": kicked}


async def _apply_carve_ready(orch: OrchestratorCommands, payload: dict[str, Any]) -> dict[str, Any]:
    ticket_id = payload.get("ticket_id")
    if not isinstance(ticket_id, str) or not ticket_id.strip():
        raise ValueError("ticket.apply_carve_ready requires ticket_id")
    carve = payload.get("carve")
    if not (isinstance(carve, dict) and carve):
        raise ValueError("ticket.apply_carve_ready requires non-empty 'carve' in payload")
    return await orch.apply_ticket_carve_ready(ticket_id.strip(), payload)


async def _capture_submit(orch: OrchestratorCommands, payload: dict[str, Any]) -> dict[str, Any]:
    result = await orch.submit_notetaker_capture(dict(payload))
    return {"handled": True, **result}


async def _note_ensure(orch: OrchestratorCommands, payload: dict[str, Any]) -> dict[str, Any]:
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("note.ensure requires name")
    return await orch.ensure_note(name.strip())


async def _note_retire(orch: OrchestratorCommands, payload: dict[str, Any]) -> dict[str, Any]:
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("note.retire requires name")
    return await orch.retire_note(name.strip())


async def _history_dismiss(orch: OrchestratorCommands, payload: dict[str, Any]) -> dict[str, Any]:
    item_id = payload.get("item_id")
    if not isinstance(item_id, str) or not item_id.strip():
        raise ValueError("history.dismiss requires item_id")
    return await orch.dismiss_history_item(item_id.strip())


async def _history_resume(orch: OrchestratorCommands, payload: dict[str, Any]) -> dict[str, Any]:
    conversation_id = payload.get("conversation_id")
    if not isinstance(conversation_id, str) or not conversation_id.strip():
        raise ValueError("agent.resume_from_history requires conversation_id")
    return await orch.resume_conversation(conversation_id.strip())


async def _agent_message(orch: OrchestratorCommands, payload: dict[str, Any]) -> dict[str, Any]:
    agent_id = payload.get("agent_id")
    message = payload.get("message")
    if not isinstance(agent_id, str) or not agent_id.strip():
        raise ValueError("agent.message requires agent_id")
    if not isinstance(message, str):
        raise ValueError("agent.message requires message")
    ticket_id = payload.get("ticket_id")
    if ticket_id is not None and not isinstance(ticket_id, str):
        raise ValueError("agent.message ticket_id must be a string or null")
    return await orch.send_agent_message(agent_id.strip(), message, ticket_id)


async def _agent_send_key(orch: OrchestratorCommands, payload: dict[str, Any]) -> dict[str, Any]:
    raw_agent_id = payload.get("agent_id")
    if raw_agent_id is not None and (
        not isinstance(raw_agent_id, str) or not raw_agent_id.strip()
    ):
        raise ValueError("agent.send_key agent_id must be a string or null")
    key = payload.get("key")
    if not isinstance(key, str) or not key.strip():
        raise ValueError("agent.send_key requires key")
    literal = bool(payload.get("literal"))
    enter = bool(payload.get("enter"))
    log_user_input = payload.get("log_user_input")
    if log_user_input is not None and not isinstance(log_user_input, str):
        raise ValueError("agent.send_key log_user_input must be a string or null")
    agent_id = (
        raw_agent_id.strip()
        if isinstance(raw_agent_id, str) and raw_agent_id.strip()
        else None
    )
    return await orch.send_agent_key(
        agent_id,
        key.strip(),
        literal=literal,
        enter=enter,
        log_user_input=log_user_input,
    )


async def _agent_transcript_refresh(
    orch: OrchestratorCommands, payload: dict[str, Any]
) -> dict[str, Any]:
    agent_id = payload.get("agent_id")
    if not isinstance(agent_id, str) or not agent_id.strip():
        raise ValueError("agent.transcript.refresh requires agent_id")
    return await orch.refresh_agent_transcript(agent_id.strip())


async def _agent_interrupt(orch: OrchestratorCommands, payload: dict[str, Any]) -> dict[str, Any]:
    agent_id = payload.get("agent_id")
    if not isinstance(agent_id, str) or not agent_id.strip():
        raise ValueError("agent.interrupt requires agent_id")
    return await orch.interrupt_agent(agent_id.strip())


async def _agent_stop(orch: OrchestratorCommands, payload: dict[str, Any]) -> dict[str, Any]:
    agent_id = payload.get("agent_id")
    if not isinstance(agent_id, str) or not agent_id.strip():
        raise ValueError("agent.stop requires agent_id")
    return await orch.stop_agent(agent_id.strip())


async def _rename_rogue(orch: OrchestratorCommands, payload: dict[str, Any]) -> dict[str, Any]:
    agent_id = payload.get("agent_id")
    name = payload.get("name")
    if not isinstance(agent_id, str) or not agent_id.strip():
        raise ValueError("crow.rename_rogue requires agent_id")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("crow.rename_rogue requires name")
    return await orch.rename_rogue_agent(agent_id.strip(), name.strip())


async def _scaffold_plan(orch: OrchestratorCommands, payload: dict[str, Any]) -> dict[str, Any]:
    name = payload.get("name")
    body = payload.get("body")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("plan.scaffold requires name")
    if not isinstance(body, str):
        raise ValueError("plan.scaffold requires body")
    return await orch.scaffold_plan(name.strip(), body)


async def _rename_plan(orch: OrchestratorCommands, payload: dict[str, Any]) -> dict[str, Any]:
    old_name = payload.get("old_name")
    new_name = payload.get("new_name")
    if not isinstance(old_name, str) or not old_name.strip():
        raise ValueError("plan.rename requires old_name")
    if not isinstance(new_name, str) or not new_name.strip():
        raise ValueError("plan.rename requires new_name")
    return await orch.rename_plan(old_name.strip(), new_name.strip())


async def _deprecate_plan(orch: OrchestratorCommands, payload: dict[str, Any]) -> dict[str, Any]:
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("plan.deprecate requires name")
    return await orch.deprecate_plan(name.strip())


async def _retry_failed(orch: OrchestratorCommands, payload: dict[str, Any]) -> dict[str, Any]:
    ticket_id = payload.get("ticket_id")
    if not isinstance(ticket_id, str) or not ticket_id.strip():
        raise ValueError("ticket.retry_failed requires ticket_id")
    return await orch.retry_failed_ticket(ticket_id.strip())


async def _crow_reset(orch: OrchestratorCommands, payload: dict[str, Any]) -> dict[str, Any]:
    ticket_id = payload.get("ticket_id")
    if not isinstance(ticket_id, str) or not ticket_id.strip():
        raise ValueError("crow.reset requires ticket_id")
    return await orch.reset_crow(ticket_id.strip())


async def _set_schedule_at(orch: OrchestratorCommands, payload: dict[str, Any]) -> dict[str, Any]:
    ticket_id = payload.get("ticket_id")
    schedule_at = payload.get("schedule_at")
    if not isinstance(ticket_id, str) or not ticket_id.strip():
        raise ValueError("ticket.set_schedule_at requires ticket_id")
    if schedule_at is not None and not isinstance(schedule_at, str):
        raise ValueError("ticket.set_schedule_at requires string or null schedule_at")
    return await orch.set_schedule_at(ticket_id.strip(), schedule_at)


async def _update_metadata(orch: OrchestratorCommands, payload: dict[str, Any]) -> dict[str, Any]:
    ticket_id = payload.get("ticket_id")
    if not isinstance(ticket_id, str) or not ticket_id.strip():
        raise ValueError("ticket.update_metadata requires ticket_id")
    return await orch.update_ticket_metadata(ticket_id.strip(), dict(payload))


async def _force_status(orch: OrchestratorCommands, payload: dict[str, Any]) -> dict[str, Any]:
    ticket_id = payload.get("ticket_id")
    status = payload.get("status")
    if not isinstance(ticket_id, str) or not ticket_id.strip():
        raise ValueError("ticket.force_status requires ticket_id")
    if not isinstance(status, str) or not status.strip():
        raise ValueError("ticket.force_status requires status")
    return await orch.force_ticket_status(ticket_id.strip(), status.strip())


async def _quick_kick(orch: OrchestratorCommands, payload: dict[str, Any]) -> dict[str, Any]:
    title = payload.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("ticket.quick_kick requires title")
    return await orch.quick_kick_ticket(title.strip())


async def _quick_create(orch: OrchestratorCommands, payload: dict[str, Any]) -> dict[str, Any]:
    title = payload.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("ticket.quick_create requires title")
    return orch.quick_create_ticket(title.strip())


async def _spawn_rogue(orch: OrchestratorCommands, payload: dict[str, Any]) -> dict[str, Any]:
    return await orch.spawn_rogue_command(dict(payload))


async def _spawn_planner(orch: OrchestratorCommands, payload: dict[str, Any]) -> dict[str, Any]:
    return await orch.spawn_planner_command(dict(payload))


async def _reconfigure_collaborator(
    orch: OrchestratorCommands, payload: dict[str, Any]  # noqa: ARG001
) -> dict[str, Any]:
    return await orch.reconfigure_collaborator()


# Command kind -> handler. Adding a command kind is now one table entry (plus the
# matching ``accepts`` string) instead of a constructor kwarg + elif + bootstrap
# wire. Each handler performs the payload-field extraction its kind needs and
# returns the orchestrator method's result verbatim.
_HANDLERS: dict[str, Handler] = {
    "scheduler.kickoff_ready": _kickoff_ready,
    "ticket.apply_carve_ready": _apply_carve_ready,
    "notetaker.capture.submit": _capture_submit,
    "note.ensure": _note_ensure,
    "note.retire": _note_retire,
    "history.dismiss": _history_dismiss,
    "agent.resume_from_history": _history_resume,
    "agent.message": _agent_message,
    "agent.send_key": _agent_send_key,
    "agent.transcript.refresh": _agent_transcript_refresh,
    "agent.interrupt": _agent_interrupt,
    "agent.stop": _agent_stop,
    "crow.rename_rogue": _rename_rogue,
    "crow.reset": _crow_reset,
    "plan.scaffold": _scaffold_plan,
    "plan.rename": _rename_plan,
    "plan.deprecate": _deprecate_plan,
    "ticket.retry_failed": _retry_failed,
    "ticket.set_schedule_at": _set_schedule_at,
    "ticket.update_metadata": _update_metadata,
    "ticket.force_status": _force_status,
    "ticket.quick_kick": _quick_kick,
    "ticket.quick_create": _quick_create,
    "crow.spawn_rogue": _spawn_rogue,
    "planner.spawn": _spawn_planner,
    "collaborator.reconfigure": _reconfigure_collaborator,
}


async def dispatch_orchestrator_command(
    orchestrator: OrchestratorCommands,
    kind: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Execute an orchestration use case without publishing a bus command."""

    handler = _HANDLERS.get(kind)
    if handler is None:
        raise ValueError(f"unsupported orchestration command {kind!r}")
    return await handler(orchestrator, payload)


class OrchestratorCommandWorker(Worker):
    def __init__(self, orchestrator: OrchestratorCommands) -> None:
        super().__init__(
            WorkerSpec(
                name="orchestrator",
                process_model="thread",
                accepts=tuple(_HANDLERS),
            )
        )
        self._orch = orchestrator

    async def run(self, ctx: WorkerCtx, stop_event: asyncio.Event) -> None:  # noqa: ARG002
        await stop_event.wait()

    async def on_command(self, command: CommandEvent, ctx: WorkerCtx) -> dict[str, Any]:  # noqa: ARG002
        if command.kind not in _HANDLERS:
            # Wiring-miss signal (plan-command-result-contract): a kind reached
            # this worker with no handler. Keep the three-way contract's "not
            # handled here" return distinct from a domain failure.
            return {"handled": False}
        return await dispatch_orchestrator_command(
            self._orch, command.kind, command.payload
        )
