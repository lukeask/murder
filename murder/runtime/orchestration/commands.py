"""Closed private commands exchanged by durable orchestration workers."""

from __future__ import annotations

from enum import Enum


class OrchestrationCommand(str, Enum):
    AGENT_INTERRUPT = "agent.interrupt"
    AGENT_MESSAGE = "agent.message"
    AGENT_RESUME_FROM_HISTORY = "agent.resume_from_history"
    AGENT_SEND_KEY = "agent.send_key"
    AGENT_STOP = "agent.stop"
    AGENT_TRANSCRIPT_REFRESH = "agent.transcript.refresh"
    COLLABORATOR_CHAT_SEND = "collaborator.chat_send"
    COLLABORATOR_RECONFIGURE = "collaborator.reconfigure"
    COLLABORATOR_SWAP_MODEL = "collaborator.swap_model"
    COLLABORATOR_TRANSCRIPT_REFRESH = "collaborator.transcript.refresh"
    CONFIG_HARNESSES_CHANGED = "config.harnesses_changed"
    CROW_RENAME_ROGUE = "crow.rename_rogue"
    CROW_RESET = "crow.reset"
    CROW_SPAWN_ROGUE = "crow.spawn_rogue"
    HISTORY_DISMISS = "history.dismiss"
    NOTE_ENSURE = "note.ensure"
    NOTE_RETIRE = "note.retire"
    NOTETAKER_CAPTURE_SUBMIT = "notetaker.capture.submit"
    PLAN_DEPRECATE = "plan.deprecate"
    PLAN_RENAME = "plan.rename"
    PLAN_SCAFFOLD = "plan.scaffold"
    PLANNER_SPAWN = "planner.spawn"
    SCHEDULER_KICKOFF_READY = "scheduler.kickoff_ready"
    SCHEDULER_SET_MODE = "scheduler.set_mode"
    SCHEDULER_SET_PARAMS = "scheduler.set_params"
    SCHEDULER_SET_STEERING = "scheduler.set_steering"
    STATE_ESCALATION_ACK = "state.escalation.ack"
    STATE_ESCALATION_CREATE = "state.escalation.create"
    STATE_HARNESS_USAGE_SAMPLE = "state.harness_usage.sample"
    STATE_HARNESS_VERSION_PROBE = "state.harness_version.probe"
    TICKET_APPLY_CARVE_READY = "ticket.apply_carve_ready"
    TICKET_FORCE_STATUS = "ticket.force_status"
    TICKET_QUICK_CREATE = "ticket.quick_create"
    TICKET_QUICK_KICK = "ticket.quick_kick"
    TICKET_RETRY_FAILED = "ticket.retry_failed"
    TICKET_SET_SCHEDULE_AT = "ticket.set_schedule_at"
    TICKET_UPDATE_METADATA = "ticket.update_metadata"

    def __str__(self) -> str:
        return str.__str__(self)


__all__ = ["OrchestrationCommand"]
