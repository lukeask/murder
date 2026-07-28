"""Reusable, userspace-global workflow definitions.

A workflow template (``WorkflowTemplate`` / ``WorkflowDef``) describes a
pipeline of agent *nodes* that later get materialized into project tickets
(materialization lives elsewhere). This package owns only the definition model
and its pure validation; storage and RPC plumbing mirror the templates registry
in ``murder.user_config`` / the service host.

Prefer ``WorkflowTemplate`` and ``WorkflowNodeTemplate`` in new code; the
``WorkflowDef`` / ``StageDef`` names remain the concrete classes and the names
used in persisted/API payloads for now.
"""

from __future__ import annotations

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

__all__ = [
    "CompileWorkflowTemplateParams",
    "CompileWorkflowTemplateResult",
    "StageDef",
    "WorkflowCompileIssue",
    "WorkflowDef",
    "WorkflowInput",
    "WorkflowInputDecl",
    "WorkflowIssue",
    "WorkflowNodeTemplate",
    "WorkflowTemplate",
    "compile_workflow_template",
    "validate_workflow",
    "workflow_issues",
]
