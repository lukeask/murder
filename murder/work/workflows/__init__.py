"""Reusable, userspace-global workflow definitions and preferred runtime aliases.

A workflow template (``WorkflowTemplate`` / ``WorkflowDef``) describes a
pipeline of agent *nodes* that later get materialized into project tickets
(materialization lives elsewhere). This package owns the definition model and
its pure validation; storage and RPC plumbing mirror the templates registry in
``murder.user_config`` / the service host.

Prefer ``WorkflowTemplate`` / ``WorkflowNodeTemplate`` and ``WorkflowRun`` /
``WorkflowNodeRun`` in new code. ``WorkflowDef`` / ``StageDef`` and
``WorkflowRunRecord`` / ``StageRunState`` remain the concrete classes (and the
names used in persisted/API payloads) for now.
"""

from __future__ import annotations

from murder.work.workflows.builtins import (
    TICKET_WORKFLOW_NAME,
    get_builtin_workflow,
    is_builtin_workflow_name,
    merge_builtin_workflows,
    prepare_workflow_for_launch,
    ticket_workflow_template,
)
from murder.work.workflows.compile import (
    CompileWorkflowTemplateParams,
    CompileWorkflowTemplateResult,
    WorkflowCompileIssue,
    WorkflowInput,
    compile_workflow_template,
)
from murder.work.workflows.definition import (
    StageDef,
    WorkflowDef,
    WorkflowInputDecl,
    WorkflowIssue,
    WorkflowNodeTemplate,
    WorkflowTemplate,
    validate_workflow,
    workflow_issues,
)
from murder.work.workflows.runtime import (
    WorkflowNodeRun,
    WorkflowRun,
)

__all__ = [
    "CompileWorkflowTemplateParams",
    "CompileWorkflowTemplateResult",
    "TICKET_WORKFLOW_NAME",
    "StageDef",
    "WorkflowCompileIssue",
    "WorkflowDef",
    "WorkflowInput",
    "WorkflowInputDecl",
    "WorkflowIssue",
    "WorkflowNodeRun",
    "WorkflowNodeTemplate",
    "WorkflowRun",
    "WorkflowTemplate",
    "compile_workflow_template",
    "get_builtin_workflow",
    "is_builtin_workflow_name",
    "merge_builtin_workflows",
    "prepare_workflow_for_launch",
    "ticket_workflow_template",
    "validate_workflow",
    "workflow_issues",
]
