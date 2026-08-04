"""Built-in feature handlers, grouped by namespace."""

from __future__ import annotations

from typing import TYPE_CHECKING

from murder.app.service.application import ApplicationRegistrar
from murder.app.service.document_editors import DocumentEditorService
from murder.app.service.projection_registry import ProjectionProviderRegistry
from murder.runtime.sessions.service import SessionService

from . import (
    command,
    document_editor,
    harness_control,
    health,
    image,
    llm_settings,
    plan,
    report,
    roster,
    settings,
    state,
    ticket,
    trigger,
    tui,
    worktree,
)
from .approvals import ApprovalUseCases
from .approvals import register as register_approvals
from .sessions import register as register_sessions
from .workflows import WorkflowUseCases
from .workflows import register as register_workflows

if TYPE_CHECKING:
    from murder.app.service.host import ServiceHost


def register_all(
    app: ApplicationRegistrar,
    *,
    projections: ProjectionProviderRegistry,
    document_editors: DocumentEditorService,
    sessions: SessionService,
    workflows: WorkflowUseCases,
    approvals: ApprovalUseCases,
    legacy_host: ServiceHost,
) -> None:
    """Compose built-in features at the application boundary.

    Stateful vertical slices receive explicit non-null services. Remaining
    handlers still take ``legacy_host`` until they migrate off ServiceHost digs.
    """
    register_approvals(app, projections, approvals)
    health.register(legacy_host)
    harness_control.register(legacy_host)
    command.register(legacy_host)
    document_editor.register(app, document_editors)
    state.register(legacy_host, projections)
    roster.register(legacy_host, projections)
    register_sessions(app, projections, sessions)
    ticket.register(legacy_host)
    plan.register(legacy_host)
    report.register(legacy_host)
    image.register(legacy_host)
    tui.register(legacy_host, projections)
    register_workflows(app, projections, workflows)
    trigger.register(legacy_host)
    settings.register(legacy_host, projections)
    llm_settings.register(legacy_host)
    worktree.register(legacy_host)
