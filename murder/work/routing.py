"""Pure synchronous routing for activity execution."""

from __future__ import annotations

from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import Field

from murder.work.activities.runtime import ExecutionRoute, ModelAssignment, SessionStrategy
from murder.work.workflows.runtime import ExecutionRequirements, WorkflowContract


class RouteCandidate(WorkflowContract):
    harness: str = Field(min_length=1)
    models: tuple[str, ...]
    capability_tags: frozenset[str] = frozenset()
    structured_protocol: bool = False
    terminal: bool = True
    reusable_session_ids: tuple[UUID, ...] = ()
    available: bool = True
    capability_revision: int = Field(default=1, ge=1)
    usage_revision: int = Field(default=0, ge=0)


class RoutingContext(WorkflowContract):
    activity_id: UUID
    requirements: ExecutionRequirements
    candidates: tuple[RouteCandidate, ...]
    model_teams: dict[str, tuple[str, ...]] = Field(default_factory=dict)


class RoutingDecision(WorkflowContract):
    action: Literal["route", "hold"]
    route: ExecutionRoute | None = None
    reason: str


def decide_route(context: RoutingContext) -> RoutingDecision:
    """Select execution identity without launching or reserving resources."""

    requirements = context.requirements
    candidates = [candidate for candidate in context.candidates if candidate.available]
    if requirements.preferred_harnesses:
        order = {name: index for index, name in enumerate(requirements.preferred_harnesses)}
        candidates.sort(key=lambda item: (order.get(item.harness, len(order)), item.harness))
    else:
        candidates.sort(key=lambda item: item.harness)
    for candidate in candidates:
        if candidate.harness in requirements.excluded_harnesses:
            continue
        if not requirements.capability_tags <= candidate.capability_tags:
            continue
        if requirements.require_structured_protocol and not candidate.structured_protocol:
            continue
        if requirements.require_terminal and not candidate.terminal:
            continue
        models = list(candidate.models)
        if requirements.preferred_models:
            preferred = [model for model in requirements.preferred_models if model in models]
            models = preferred + [model for model in models if model not in preferred]
        if not models:
            continue
        session_id = (
            candidate.reusable_session_ids[0]
            if requirements.reusable_session and candidate.reusable_session_ids
            else None
        )
        strategy = requirements.session_strategy
        if strategy is None:
            strategy = (
                "reuse_if_compatible" if requirements.reusable_session else "new"
            )
        if strategy == "require_existing" and session_id is None:
            continue
        team_models = next(
            (
                context.model_teams[name]
                for name in requirements.preferred_models
                if name in context.model_teams
            ),
            None,
        )
        assignment_models = (
            team_models
            if team_models and all(model in candidate.models for model in team_models)
            else (models[0],)
        )
        return RoutingDecision(
            action="route",
            route=ExecutionRoute(
                route_id=uuid5(
                    NAMESPACE_URL,
                    (
                        f"activity-route:{context.activity_id}:"
                        f"{candidate.capability_revision}:{candidate.usage_revision}"
                    ),
                ),
                assignments=tuple(
                    ModelAssignment(
                        role=("primary" if index == 0 else "reviewer"),
                        harness=candidate.harness,
                        model=model,
                    )
                    for index, model in enumerate(assignment_models)
                ),
                session_strategy=SessionStrategy(strategy),
                selected_session_id=session_id,
                structured_protocol=candidate.structured_protocol,
                terminal_fallback=candidate.terminal,
                capability_revision=candidate.capability_revision,
                usage_revision=candidate.usage_revision,
                rationale=f"selected {candidate.harness}/{models[0]}",
            ),
            reason=f"selected {candidate.harness}/{models[0]}",
        )
    return RoutingDecision(action="hold", reason="no compatible execution route")


__all__ = [
    "RouteCandidate",
    "RoutingContext",
    "RoutingDecision",
    "decide_route",
]
