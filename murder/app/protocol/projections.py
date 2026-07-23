"""Typed registry for projection snapshots and retained events."""

from __future__ import annotations

from typing import Any

from pydantic import Field, TypeAdapter

from murder.app.protocol.common import ApplicationModel
from murder.app.protocol.operations import JsonObject
from murder.app.protocol.read_models import CrowSnapshot
from murder.app.protocol.subscriptions import ProjectionTopic


class ProjectionInvalidation(ApplicationModel):
    type: str = Field(pattern=r"^projection\.invalidate$")
    projection: ProjectionTopic
    subject_key: str
    generation: int = Field(ge=0)
    source_fact_id: str | None = None


# Projection implementations are migrating from compatibility snapshots.  The
# registry makes every topic explicit now; replace JsonObject entries with the
# feature's named snapshot DTO as it moves.  No transport code has to change.
PROJECTION_SNAPSHOT_MODELS: dict[ProjectionTopic, object] = {
    topic: JsonObject for topic in ProjectionTopic
}
# Roster is the first migrated vertical slice.  Its feature-owned hydration
# snapshot has the same concrete contract as ``roster.get``.
PROJECTION_SNAPSHOT_MODELS[ProjectionTopic.ROSTER] = CrowSnapshot
PROJECTION_EVENT_MODELS: dict[ProjectionTopic, object] = {
    topic: ProjectionInvalidation for topic in ProjectionTopic
}


def validate_snapshot(topic: ProjectionTopic | str, payload: dict[str, Any]) -> dict[str, Any]:
    model = PROJECTION_SNAPSHOT_MODELS[ProjectionTopic(topic)]
    value = TypeAdapter(model).validate_python(payload)
    return TypeAdapter(model).dump_python(value, mode="json")


def validate_event(topic: ProjectionTopic | str, payload: dict[str, Any]) -> dict[str, Any]:
    model = PROJECTION_EVENT_MODELS[ProjectionTopic(topic)]
    value = TypeAdapter(model).validate_python(payload)
    return TypeAdapter(model).dump_python(value, mode="json")
