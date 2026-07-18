"""Projection and notification subscription contracts."""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal

from pydantic import Field

from murder.app.protocol.common import ApplicationModel


class StrEnum(str, Enum):
    """Python-3.10-compatible string enum."""

    def __str__(self) -> str:
        return str.__str__(self)


class ProjectionTopic(StrEnum):
    CONVERSATIONS = "conversations"
    ROSTER = "roster"
    SCHEDULE = "schedule"
    FAVORITES = "favorites"
    TEMPLATES = "templates"
    THEMES = "themes"
    WORKFLOWS = "workflows"
    SETTINGS = "settings"


class NotificationChannel(StrEnum):
    ERRORS = "errors"
    PRESENCE = "presence"


class ProjectionSubscription(ApplicationModel):
    kind: Literal["projections"] = "projections"
    topics: list[ProjectionTopic]
    cursor: int | None = Field(default=None, ge=0)


class NotificationSubscription(ApplicationModel):
    kind: Literal["notifications"] = "notifications"
    channels: list[NotificationChannel]
    cursor: int | None = Field(default=None, ge=0)


SubscriptionSpec = Annotated[
    ProjectionSubscription | NotificationSubscription,
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
