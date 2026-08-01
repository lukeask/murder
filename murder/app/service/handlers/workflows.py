"""Typed workflow run inspection, signaling, and compile application handlers."""

from __future__ import annotations

from typing import Any, Protocol
from uuid import uuid4

from murder.app.protocol.requests import CommandName, QueryName
from murder.app.protocol.subscriptions import ProjectionTopic
from murder.app.protocol.workflows import (
    CompileWorkflowParams,
    GetWorkflowRunParams,
    ListWorkflowRunsParams,
    SignalWorkflowParams,
)
from murder.app.service.application import ApplicationRegistrar
from murder.app.service.projection_registry import ProjectionProviderRegistry
from murder.state.persistence.connection import RepoDb
from murder.state.persistence.workflow_runs import (
    get_workflow_run,
    list_workflow_runs,
    list_workflow_waits,
)
from murder.work.workflows.compile import (
    compile_workflow_template,
    prompt_template_map,
)
from murder.work.workflows.definition import WorkflowDef
from murder.work.workflows.service import WorkflowRuntime


class WorkflowEffects(Protocol):
    """Runtime capabilities required by workflow-run use cases."""

    db: RepoDb | None


def register(
    app: ApplicationRegistrar,
    projections: ProjectionProviderRegistry,
    effects: WorkflowEffects,
) -> None:
    """Register workflow-run use cases and their feature-owned snapshot."""

    def _db() -> RepoDb:
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
        db = _db()
        run = get_workflow_run(db, params.workflow_id)
        if run is None:
            return {"ok": False, "run": None, "waits": [], "error": "not_found"}
        waits = list_workflow_waits(db, params.workflow_id) if params.include_waits else []
        return {
            "ok": True,
            "run": run.model_dump(mode="json"),
            "waits": [wait.model_dump(mode="json") for wait in waits],
        }

    def _compile(body: dict[str, Any]) -> dict[str, Any]:
        params = CompileWorkflowParams.model_validate(body)
        if params.template is not None:
            template = params.template
        else:
            assert params.name is not None  # validated by CompileWorkflowParams
            template = _load_workflow_by_name(params.name)
        templates = (
            params.prompt_templates
            if params.prompt_templates is not None
            else prompt_template_map()
        )
        result = compile_workflow_template(template, prompt_templates=templates)
        return result.model_dump(mode="json")

    def _signal(body: dict[str, Any]) -> dict[str, Any]:
        params = SignalWorkflowParams.model_validate(body)
        db = _db()
        deduplication_key = params.deduplication_key or (
            f"external:{params.name}:{params.correlation_key or ''}:{params.request_id or uuid4()}"
        )
        signal, run = WorkflowRuntime(db).enqueue_and_wake(
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
    app.register_application_query(QueryName.WORKFLOW_COMPILE, _compile)
    app.register_application_command(CommandName.WORKFLOW_SIGNAL, _signal)
    projections.register(ProjectionTopic.WORKFLOW_RUNS, lambda: _runs_list({}))


def _load_workflow_by_name(name: str) -> WorkflowDef:
    """Resolve a workflow template by name (built-ins first, then userspace).

    Mirrors ``run_workflow_by_name`` so ``workflow.compile`` by name can preview
    the built-in ``ticket`` template the same way ``workflow.start`` launches it.
    """
    from murder.user_config import load_workflows  # noqa: PLC0415
    from murder.work.workflows.builtins import get_builtin_workflow  # noqa: PLC0415

    builtin = get_builtin_workflow(name)
    if builtin is not None:
        return builtin

    found: dict | None = None
    for record in load_workflows():
        if record.get("name") == name:
            found = record
    if found is None:
        raise ValueError(f"no saved workflow named {name!r}")
    return WorkflowDef.model_validate(found)


__all__ = ["WorkflowEffects", "register"]
