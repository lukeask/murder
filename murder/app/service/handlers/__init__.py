"""Built-in feature handlers, grouped by namespace."""

from __future__ import annotations

from pathlib import Path

from murder.app.service.application import ApplicationRegistrar
from murder.app.service.document_editors import DocumentEditorService
from murder.app.service.projection_registry import ProjectionProviderRegistry
from murder.app.service.read_model import ServiceReadModel
from murder.app.service.usage_sampling import UsageSamplingService
from murder.config import Config
from murder.roster.service import RosterService
from murder.runtime.orchestration.orchestrator import Orchestrator
from murder.runtime.orchestration.structured_decisions import StructuredDecisionRouter
from murder.runtime.sessions.service import SessionService
from murder.state.persistence.connection import RepoDb

from . import (
    command,
    document_editor,
    harness_control,
    health,
    image,
    llm_settings,
    orchestration,
    plan,
    report,
    roster,
    scheduler,
    settings,
    state,
    ticket,
    trigger,
    tui,
    usage,
    worktree,
)
from .approvals import ApprovalUseCases
from .approvals import register as register_approvals
from .sessions import register as register_sessions
from .workflows import WorkflowUseCases
from .workflows import register as register_workflows


def register_all(
    app: ApplicationRegistrar,
    *,
    projections: ProjectionProviderRegistry,
    document_editors: DocumentEditorService,
    sessions: SessionService,
    workflows: WorkflowUseCases,
    approvals: ApprovalUseCases,
    db: RepoDb,
    repo_root: Path,
    run_id: str,
    config: Config,
    read_model: ServiceReadModel,
    orchestrator: Orchestrator,
    roster_service: RosterService,
    structured_decisions: StructuredDecisionRouter,
) -> None:
    """Compose built-in features at the application boundary.

    Handlers receive exact feature dependencies — never RepositoryHost.
    """
    register_approvals(app, projections, approvals)
    health.register(app, run_id=run_id)
    harness_control.register(app, structured_decisions)
    command.register(app, db)
    document_editor.register(app, document_editors)
    state.register(app, projections, read_model)
    roster.register(app, projections, roster=roster_service, db=db)
    register_sessions(app, projections, sessions)
    ticket.register(app, orchestrator)
    plan.register(app, orchestrator)
    report.register(app, db=db, repo_root=repo_root)
    image.register(app, repo_root)
    tui.register(
        app,
        projections,
        repo_root=repo_root,
        db=db,
        orchestrator=orchestrator,
    )
    register_workflows(app, projections, workflows)
    trigger.register(app, db)
    settings.register(app, projections, repo_root=repo_root, config=config)
    llm_settings.register(app, repo_root=repo_root, config=config)
    worktree.register(app, repo_root)
    orchestration.register(app, orchestrator)
    scheduler.register(app, db)
    usage.register(app, UsageSamplingService(repo_root, db))
