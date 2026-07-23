"""Built-in feature handlers, grouped by namespace."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from murder.app.service.application import ApplicationRegistrar
from murder.app.service.projection_registry import ProjectionProviderRegistry

from murder.app.service.handlers import (
    approvals,
    command,
    harness_control,
    health,
    image,
    plan,
    roster,
    sessions,
    settings,
    state,
    ticket,
    trigger,
    tui,
    workflows,
    worktree,
)

if TYPE_CHECKING:
    from murder.app.service.host import ServiceHost


def register_all(
    app: ApplicationRegistrar,
    *,
    projections: ProjectionProviderRegistry | None = None,
    effects: object | None = None,
) -> None:
    """Compose built-in features at the application boundary.

    The three stateful vertical slices receive only the application registrar,
    projection registry, and their runtime effects.  ``effects`` defaults to
    ``app`` only for the lightweight registration test seam; production passes
    the runtime explicitly from the composition root.
    """
    feature_projections = projections or ProjectionProviderRegistry()
    feature_effects = app if effects is None else effects

    approvals.register(
        app,
        feature_projections,
        cast(approvals.ApprovalEffects, feature_effects),
    )
    legacy_host = cast("ServiceHost", app)
    health.register(legacy_host)
    harness_control.register(legacy_host)
    command.register(legacy_host)
    state.register(legacy_host, projections)
    roster.register(legacy_host, projections)
    sessions.register(
        app,
        feature_projections,
        cast(sessions.SessionEffects, feature_effects),
    )
    ticket.register(legacy_host)
    plan.register(legacy_host)
    image.register(legacy_host)
    tui.register(legacy_host, projections)
    workflows.register(
        app,
        feature_projections,
        cast(workflows.WorkflowEffects, feature_effects),
    )
    trigger.register(legacy_host)
    settings.register(legacy_host, projections)
    worktree.register(legacy_host)
