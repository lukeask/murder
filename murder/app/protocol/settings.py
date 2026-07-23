"""Typed settings, LLM, and TUI-prefs application contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, Field, JsonValue

from murder.app.protocol.common import ApplicationModel


# --- Queries -----------------------------------------------------------------


class GetSettingsParams(ApplicationModel):
    """Empty params object for ``settings.get``."""


class GetSettingsResult(ApplicationModel):
    ok: Literal[True] = True
    settings: dict[str, JsonValue]


class GetFavoritesParams(ApplicationModel):
    """Empty params object for ``favorites.get``."""


class GetFavoritesResult(ApplicationModel):
    ok: Literal[True] = True
    favorites: list[str]


class GetSpawnFavoritesParams(ApplicationModel):
    """Empty params object for ``spawn_favorites.get``."""


class GetSpawnFavoritesResult(ApplicationModel):
    ok: Literal[True] = True
    favorites: list[dict[str, JsonValue]]


class GetTemplatesParams(ApplicationModel):
    """Empty params object for ``templates.get``."""


class GetTemplatesResult(ApplicationModel):
    ok: Literal[True] = True
    templates: list[dict[str, JsonValue]]


class GetThemesParams(ApplicationModel):
    """Empty params object for ``themes.get``."""


class GetThemesResult(ApplicationModel):
    ok: Literal[True] = True
    themes: list[dict[str, JsonValue]]


# --- Shared LLM mutation result ----------------------------------------------


class LlmMutationResult(ApplicationModel):
    ok: Literal[True] = True
    llm: dict[str, JsonValue]
    settings: dict[str, JsonValue]
    provider_id: str | None = None
    policy_id: str | None = None


class CreateLlmProviderResult(LlmMutationResult):
    provider_id: str


class CreateLlmPolicyResult(LlmMutationResult):
    policy_id: str


class CloneLlmPolicyResult(LlmMutationResult):
    policy_id: str


# --- Settings / LLM commands -------------------------------------------------


class UpdateSettingsParams(ApplicationModel):
    settings: dict[str, JsonValue]


class UpdateSettingsResult(ApplicationModel):
    ok: Literal[True] = True
    settings: dict[str, JsonValue]


class SetLlmDisabledParams(ApplicationModel):
    disabled: bool


class CreateLlmProviderParams(ApplicationModel):
    provider: dict[str, JsonValue]


class UpdateLlmProviderParams(ApplicationModel):
    provider_id: str = Field(min_length=1)
    patch: dict[str, JsonValue]


class DeleteLlmProviderParams(ApplicationModel):
    provider_id: str = Field(min_length=1)
    confirm: Literal[True]


class UpdateLlmProviderModelsParams(ApplicationModel):
    provider_id: str = Field(min_length=1)
    patch: dict[str, JsonValue]


class DiscoverLlmProviderModelsParams(ApplicationModel):
    provider_id: str = Field(min_length=1)


class DiscoveredLlmModel(ApplicationModel):
    id: str
    label: str


class DiscoverLlmProviderModelsResult(ApplicationModel):
    ok: Literal[True] = True
    models: list[DiscoveredLlmModel]


class CreateLlmPolicyParams(ApplicationModel):
    name: str = Field(min_length=1)
    policy: dict[str, JsonValue] | None = None


class UpdateLlmPolicyParams(ApplicationModel):
    policy_id: str = Field(min_length=1)
    patch: dict[str, JsonValue]


class DeleteLlmPolicyParams(ApplicationModel):
    policy_id: str = Field(min_length=1)
    confirm: Literal[True]


class ActivateLlmPolicyParams(ApplicationModel):
    policy_id: str = Field(min_length=1)


class CloneLlmPolicyParams(ApplicationModel):
    policy_id: str = Field(min_length=1)
    name: str = Field(min_length=1)


class SetLlmFeaturePolicyParams(ApplicationModel):
    feature_type: str = Field(min_length=1)
    policy_id: str | None = None


class PreviewLlmResolutionParams(ApplicationModel):
    feature_type: str = Field(min_length=1)
    required_capabilities: list[str] = Field(default_factory=list)
    required_execution_mode: str | None = None
    min_context_tokens: int | None = Field(default=None, ge=1)


class LlmResolutionCandidate(ApplicationModel):
    provider_id: str
    provider_type: str
    model_id: str
    locality: str
    cost_class: str


class PreviewLlmResolutionResult(ApplicationModel):
    ok: Literal[True] = True
    status: str
    policy_id: str | None
    candidates: list[LlmResolutionCandidate]


# --- Favorites / templates / themes ------------------------------------------


class SetFavoritesParams(ApplicationModel):
    favorites: list[str]


class SetFavoritesResult(ApplicationModel):
    ok: Literal[True] = True
    favorites: list[str]


class SetSpawnFavoritesParams(ApplicationModel):
    favorites: list[dict[str, JsonValue]]


class SetSpawnFavoritesResult(ApplicationModel):
    ok: Literal[True] = True
    favorites: list[dict[str, JsonValue]]


class SetTemplatesParams(ApplicationModel):
    templates: list[dict[str, JsonValue]]


class SetTemplatesResult(ApplicationModel):
    ok: Literal[True] = True
    templates: list[dict[str, JsonValue]]


class SetThemesParams(ApplicationModel):
    themes: list[dict[str, JsonValue]]


class SetThemesResult(ApplicationModel):
    ok: Literal[True] = True
    themes: list[dict[str, JsonValue]]


class ImportThemeParams(ApplicationModel):
    """Wire field ``json`` is the theme document; Python attr avoids BaseModel shadowing."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    theme_json: str = Field(min_length=1, validation_alias="json", serialization_alias="json")
    id: str | None = None


class ImportThemeResult(ApplicationModel):
    ok: Literal[True] = True
    themes: list[dict[str, JsonValue]]
    id: str


__all__ = [
    "ActivateLlmPolicyParams",
    "CloneLlmPolicyParams",
    "CloneLlmPolicyResult",
    "CreateLlmPolicyParams",
    "CreateLlmPolicyResult",
    "CreateLlmProviderParams",
    "CreateLlmProviderResult",
    "DeleteLlmPolicyParams",
    "DeleteLlmProviderParams",
    "DiscoverLlmProviderModelsParams",
    "DiscoverLlmProviderModelsResult",
    "DiscoveredLlmModel",
    "GetFavoritesParams",
    "GetFavoritesResult",
    "GetSettingsParams",
    "GetSettingsResult",
    "GetSpawnFavoritesParams",
    "GetSpawnFavoritesResult",
    "GetTemplatesParams",
    "GetTemplatesResult",
    "GetThemesParams",
    "GetThemesResult",
    "ImportThemeParams",
    "ImportThemeResult",
    "LlmMutationResult",
    "LlmResolutionCandidate",
    "PreviewLlmResolutionParams",
    "PreviewLlmResolutionResult",
    "SetFavoritesParams",
    "SetFavoritesResult",
    "SetLlmDisabledParams",
    "SetLlmFeaturePolicyParams",
    "SetSpawnFavoritesParams",
    "SetSpawnFavoritesResult",
    "SetTemplatesParams",
    "SetTemplatesResult",
    "SetThemesParams",
    "SetThemesResult",
    "UpdateLlmPolicyParams",
    "UpdateLlmProviderModelsParams",
    "UpdateLlmProviderParams",
    "UpdateSettingsParams",
    "UpdateSettingsResult",
]
