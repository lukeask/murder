"""Reusable, userspace-global workflow definitions.

A workflow definition describes a pipeline of agent *stages* that later get
materialized into project tickets (materialization lives elsewhere). This package
owns only the definition model and its pure validation; storage and RPC plumbing
mirror the templates registry in ``murder.user_config`` / the service host.
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
from murder.work.workflows.definition import (
    StageDef,
    WorkflowDef,
    WorkflowIssue,
    validate_workflow,
    workflow_issues,
)

__all__ = [
    "TICKET_WORKFLOW_NAME",
    "StageDef",
    "WorkflowDef",
    "WorkflowIssue",
    "get_builtin_workflow",
    "is_builtin_workflow_name",
    "merge_builtin_workflows",
    "prepare_workflow_for_launch",
    "ticket_workflow_template",
    "validate_workflow",
    "workflow_issues",
]
