"""Typed workflow run inspection and signaling application handlers."""

from __future__ import annotations

import sqlite3
from typing import Any, Protocol
from uuid import uuid4

from murder.app.protocol.requests import CommandName, QueryName
from murder.app.protocol.subscriptions import ProjectionTopic
from murder.app.protocol.workflows import (
    GetWorkflowRunParams,
    ListWorkflowRunsParams,
    SignalWorkflowParams,
)
from murder.state.persistence.workflow_runs import (
    get_workflow_run,
    list_workflow_runs,
    list_workflow_waits,
)
from murder.work.workflows.service import WorkflowRuntime

from murder.app.service.application import ApplicationRegistrar
from murder.app.service.projection_registry import ProjectionProviderRegistry


class WorkflowEffects(Protocol):
    """Runtime capabilities required by workflow-run use cases."""

    db: sqlite3.Connection | None


def register(
    app: ApplicationRegistrar,
    projections: ProjectionProviderRegistry,
    effects: WorkflowEffects,
) -> None:
    """Register workflow-run use cases and their feature-owned snapshot."""
    def _db() -> sqlite3.Connection:
        connection = effects.db
        if connection is None:
            raise RuntimeError("service not started")
        return connection

    def _runs_list(body: dict[str, Any]) -> dict[str, Any]:
        params = ListWorkflowRunsParams.model_validate(body)
        runs = list_workflow_runs(_db())
        if params.status is not None:
            runs = [run for run in runs if run.status == params.status]
        if params.definition_name is not None:
            runs = [run for run in runs if run.definition_name == params.definition_name]
        runs = runs[: params.limit]
        return {"runs": [run.model_dump(mode="json") for run in runs]}

    def _runs_get(body: dict[str, Any]) -> dict[str, Any]:
        params = GetWorkflowRunParams.model_validate(body)
        connection = _db()
        run = get_workflow_run(connection, params.workflow_id)
        if run is None:
            return {"ok": False, "run": None, "waits": [], "error": "not_found"}
        waits = (
            list_workflow_waits(connection, params.workflow_id)
            if params.include_waits
            else []
        )
        return {
            "ok": True,
            "run": run.model_dump(mode="json"),
            "waits": [wait.model_dump(mode="json") for wait in waits],
        }

    def _signal(body: dict[str, Any]) -> dict[str, Any]:
        params = SignalWorkflowParams.model_validate(body)
        connection = _db()
        deduplication_key = params.deduplication_key or (
            f"external:{params.name}:{params.correlation_key or ''}:"
            f"{params.request_id or uuid4()}"
        )
        signal, run = WorkflowRuntime(connection).enqueue_and_wake(
            workflow_id=params.workflow_id,
            deduplication_key=deduplication_key,
            payload=params.external_signal(),
        )
        return {
            "signal": signal.model_dump(mode="json"),
            "run": run.model_dump(mode="json"),
        }

    app.register_application_query(QueryName.WORKFLOW_RUNS_LIST, _runs_list)
    app.register_application_query(QueryName.WORKFLOW_RUNS_GET, _runs_get)
    app.register_application_command(CommandName.WORKFLOW_SIGNAL, _signal)
    projections.register(ProjectionTopic.WORKFLOW_RUNS, lambda: _runs_list({}))


__all__ = ["WorkflowEffects", "register"]
