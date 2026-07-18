"""Pure state-machine implementation for the retained static ticket DAG type."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import JsonValue

from murder.work.workflows.definition import WorkflowDef
from murder.work.workflows.runtime import (
    ExternalSignalWait,
    ExternalWorkflowSignal,
    FactDraft,
    StageRunState,
    StageStatus,
    StateReplacement,
    StaticDagWorkflowStateV1,
    WorkflowSignalRecord,
    WorkflowStatus,
    WorkflowTransitionPlan,
    WorkflowWaitRecord,
    versioned_state,
)

_TERMINAL_STAGE_STATUSES = {
    StageStatus.SUCCEEDED,
    StageStatus.FAILED,
    StageStatus.CANCELLED,
}


class StaticDagWorkflowMachine:
    """Interpret ticket completion signals without reading ticket rows.

    Ticket creation/scheduling remains a compatibility activity outside this
    class.  The machine's only inputs are its immutable definition snapshot,
    current typed state, current waits, and a finite signal batch.
    """

    state_model = StaticDagWorkflowStateV1

    def __init__(self, definition: WorkflowDef, stage_map: dict[str, str]) -> None:
        self.definition = definition.model_copy(deep=True)
        self.stage_map = dict(stage_map)
        self.definition_name = definition.name
        self.definition_version = definition.definition_version

    def initialize(
        self,
        *,
        inputs: dict[str, JsonValue],
        now: datetime,
    ) -> StaticDagWorkflowStateV1:
        del now
        return StaticDagWorkflowStateV1(
            inputs=inputs,
            stages=tuple(
                StageRunState(
                    stage_id=stage.id,
                    status=(StageStatus.READY if not stage.depends_on else StageStatus.BLOCKED),
                )
                for stage in self.definition.stages
            ),
        )

    def decide(  # noqa: PLR0912, PLR0915 - state transition cases are kept together
        self,
        *,
        state: StaticDagWorkflowStateV1,
        waits: tuple[WorkflowWaitRecord, ...],
        signals: tuple[WorkflowSignalRecord, ...],
        now: datetime,
        current_revision: int,
    ) -> WorkflowTransitionPlan:
        del waits, now
        by_stage = {stage.stage_id: stage for stage in state.stages}
        ticket_to_stage = {ticket_id: stage_id for stage_id, ticket_id in self.stage_map.items()}
        consumed: list[UUID] = []

        for signal in signals:
            payload = signal.payload
            if (
                not isinstance(payload, ExternalWorkflowSignal)
                or payload.name != "ticket.finished"
                or payload.correlation_key not in ticket_to_stage
            ):
                # The signal is addressed to this exact workflow/version, so
                # this machine owns its disposition even when it causes no
                # transition. Consumption does not imply causation.
                consumed.append(signal.signal_id)
                continue
            stage_id = ticket_to_stage[payload.correlation_key]
            existing = by_stage.get(stage_id)
            if existing is None or existing.status in _TERMINAL_STAGE_STATUSES:
                consumed.append(signal.signal_id)
                continue
            ticket_status = str(payload.payload.get("status", "done"))
            if ticket_status in {"done", "archived", "succeeded"}:
                status = StageStatus.SUCCEEDED
                error = None
            elif ticket_status in {"cancelled", "canceled"}:
                status = StageStatus.CANCELLED
                error = _optional_text(payload.payload.get("reason"))
            else:
                status = StageStatus.FAILED
                error = _optional_text(
                    payload.payload.get("error", f"ticket finished as {ticket_status}")
                )
            by_stage[stage_id] = existing.model_copy(update={"status": status, "error": error})
            consumed.append(signal.signal_id)

        # A child becomes ready only from authoritative persisted predecessor
        # state, never by consulting mutable ticket statuses.
        changed = True
        while changed:
            changed = False
            for stage_def in self.definition.stages:
                stage = by_stage[stage_def.id]
                if stage.status != StageStatus.BLOCKED:
                    continue
                dependency_statuses = {
                    by_stage[dependency].status for dependency in stage_def.depends_on
                }
                if dependency_statuses and dependency_statuses <= {StageStatus.SUCCEEDED}:
                    by_stage[stage_def.id] = stage.model_copy(update={"status": StageStatus.READY})
                    changed = True
                elif dependency_statuses & {
                    StageStatus.FAILED,
                    StageStatus.CANCELLED,
                }:
                    by_stage[stage_def.id] = stage.model_copy(
                        update={
                            "status": StageStatus.CANCELLED,
                            "error": "an upstream stage did not succeed",
                        }
                    )
                    changed = True

        ordered = tuple(by_stage[stage.id] for stage in self.definition.stages)
        replacement_state = state.model_copy(update={"stages": ordered})
        statuses = {stage.status for stage in ordered}
        if ordered and statuses <= {StageStatus.SUCCEEDED}:
            workflow_status = WorkflowStatus.COMPLETED
            terminal_reason = None
        elif statuses & {StageStatus.FAILED}:
            workflow_status = WorkflowStatus.FAILED
            terminal_reason = "one or more static DAG stages failed"
        elif ordered and statuses <= _TERMINAL_STAGE_STATUSES:
            workflow_status = WorkflowStatus.CANCELLED
            terminal_reason = "static DAG could not complete"
        else:
            workflow_status = WorkflowStatus.WAITING
            terminal_reason = None

        replacement_waits = (
            ()
            if workflow_status
            in {
                WorkflowStatus.COMPLETED,
                WorkflowStatus.FAILED,
                WorkflowStatus.CANCELLED,
            }
            else tuple(
                ExternalSignalWait(
                    signal_name="ticket.finished",
                    correlation_key=self.stage_map[stage.stage_id],
                )
                for stage in ordered
                if stage.status not in _TERMINAL_STAGE_STATUSES
            )
        )
        facts = (
            (
                FactDraft(
                    kind=f"workflow.{workflow_status.value}",
                    payload={"definition_name": self.definition_name},
                ),
            )
            if workflow_status
            in {
                WorkflowStatus.COMPLETED,
                WorkflowStatus.FAILED,
                WorkflowStatus.CANCELLED,
            }
            else ()
        )
        return WorkflowTransitionPlan(
            state=StateReplacement(
                expected_revision=current_revision,
                status=workflow_status,
                state=versioned_state(
                    replacement_state,
                    schema_name="static_dag",
                    schema_version=1,
                ),
                terminal_reason=terminal_reason,
            ),
            consume_signal_ids=tuple(consumed),
            replace_waits=replacement_waits,
            facts=facts,
        )


def _optional_text(value: JsonValue | None) -> str | None:
    return None if value is None else str(value)
