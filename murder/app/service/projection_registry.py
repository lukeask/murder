"""Lookup table for feature-owned projection snapshot providers."""

from __future__ import annotations

from typing import Protocol

from murder.app.protocol.subscriptions import ProjectionTopic


class ProjectionSnapshotProvider(Protocol):
    """Return the current authoritative snapshot for one projection."""

    def __call__(self) -> dict[str, object]: ...


class ProjectionProviderRegistry:
    """Register and find projection providers; it owns no projection logic."""

    def __init__(self) -> None:
        self._providers: dict[ProjectionTopic, ProjectionSnapshotProvider] = {}

    def register(self, topic: ProjectionTopic, provider: ProjectionSnapshotProvider) -> None:
        if topic in self._providers:
            raise ValueError(f"projection provider already registered for {topic.value!r}")
        self._providers[topic] = provider

    def snapshot(self, topic: ProjectionTopic | str) -> dict[str, object]:
        resolved = ProjectionTopic(topic)
        try:
            provider = self._providers[resolved]
        except KeyError as exc:
            raise LookupError(f"projection {resolved.value!r} has no registered provider") from exc
        return provider()

    def has_provider(self, topic: ProjectionTopic | str) -> bool:
        return ProjectionTopic(topic) in self._providers


__all__ = ["ProjectionProviderRegistry", "ProjectionSnapshotProvider"]
