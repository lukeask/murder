"""Projection and notification subscription contracts."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from murder.app.protocol.common import ApplicationModel, StrEnum


class ProjectionTopic(StrEnum):
    CONVERSATIONS = "conversations"
    ROSTER = "roster"
    SCHEDULE = "schedule"
    FAVORITES = "favorites"
    TEMPLATES = "templates"
    THEMES = "themes"
    WORKFLOWS = "workflows"
    WORKFLOW_RUNS = "workflow_runs"
    ACTIVITIES = "activities"
    SETTINGS = "settings"
    APPROVALS = "approvals"
    PERMISSIONS = "permissions"
    SESSIONS = "sessions"


class ProjectionSubscription(ApplicationModel):
    kind: Literal["projections"] = "projections"
    topics: list[ProjectionTopic]
    cursor: int | None = Field(default=None, ge=0)


class FactSubscription(ApplicationModel):
    """Retained immutable facts, never rows from the compatibility event bus."""

    kind: Literal["facts"] = "facts"
    fact_kinds: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        default_factory=list
    )
    cursor: int | None = Field(default=None, ge=0)


SubscriptionSpec = Annotated[
    ProjectionSubscription | FactSubscription,
    Field(discriminator="kind"),
]


class ReplayItem(ApplicationModel):
    cursor: int
    payload: dict[str, object]


class SubscriptionSnapshot(ApplicationModel):
    snapshots: dict[str, dict[str, object]] = Field(default_factory=dict)
    cursor: int
    mode: Literal["cold", "resume", "snapshot_fallback"]
    replay: list[ReplayItem] = Field(default_factory=list)
