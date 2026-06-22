"""Reusable, userspace-global workflow definitions.

A workflow definition describes a pipeline of agent *stages* that later get
materialized into project tickets (materialization lives elsewhere). This package
owns only the definition model and its pure validation; storage and RPC plumbing
mirror the templates registry in ``murder.user_config`` / the service host.
"""

from __future__ import annotations

from murder.work.workflows.definition import StageDef, WorkflowDef, validate_workflow

__all__ = ["StageDef", "WorkflowDef", "validate_workflow"]
