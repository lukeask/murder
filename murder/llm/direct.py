"""Runtime seam between direct-inference policy selection and client creation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from murder.llm.clients import catalog, resolve_role_client_tiered
from murder.llm.clients.base import APIClient
from murder.llm.policy import (
    CandidateMetadata,
    DirectLlmResolver,
    InferenceRequirements,
    Resolution,
)

if TYPE_CHECKING:
    from murder.config import ApiRoleConfig
    from murder.user_config import UserConfig


@dataclass(frozen=True)
class DirectClientResolution:
    resolution: Resolution
    client: APIClient | None
    model_id: str | None = None


def resolve_policy_client(
    user_cfg: UserConfig | None,
    feature: str,
    *,
    requirements: InferenceRequirements | None = None,
    discovered_catalogs: dict[str, dict[str, CandidateMetadata]] | None = None,
) -> DirectClientResolution:
    """Resolve a policy and build its first constructible direct client.

    Factory construction happens only after the P1 resolver has applied the
    global gate, enablement, policy, and requirements filters.  If a preferred
    candidate cannot be constructed, candidates in the same group (then lower
    groups) are tried in resolver order.
    """
    request = requirements or InferenceRequirements(feature_type=feature)
    if request.feature_type != feature:
        raise ValueError("requirements.feature_type must match feature")
    resolution = DirectLlmResolver(
        user_cfg,
        recommended_catalogs=catalog.recommended_catalogs(),
        discovered_catalogs=discovered_catalogs or {},
    ).resolve(request)
    if resolution.status != "resolved" or user_cfg is None or user_cfg.llm is None:
        return DirectClientResolution(resolution, None)
    for candidate in resolution.candidates:
        provider = user_cfg.llm.providers[candidate.provider_id]
        client = catalog.create_instance_client(candidate.provider_id, provider)
        if client is not None:
            return DirectClientResolution(resolution, client, candidate.model_id)
    return DirectClientResolution(resolution, None)


def preview_policy(
    user_cfg: UserConfig | None,
    feature: str,
    *,
    requirements: InferenceRequirements | None = None,
    discovered_catalogs: dict[str, dict[str, CandidateMetadata]] | None = None,
) -> Resolution:
    """Return a feature's effective candidates without constructing a client.

    The settings UI can use this for a candidate preview, and feature-specific
    configuration can supply capability or discovered-model constraints through
    the same public runtime seam used by direct inference.
    """
    request = requirements or InferenceRequirements(feature_type=feature)
    if request.feature_type != feature:
        raise ValueError("requirements.feature_type must match feature")
    return DirectLlmResolver(
        user_cfg,
        recommended_catalogs=catalog.recommended_catalogs(),
        discovered_catalogs=discovered_catalogs or {},
    ).preview(request)


def has_explicit_policy_config(user_cfg: UserConfig | None) -> bool:
    """Whether an empty policy result must not fall back to legacy tiering."""
    return bool(
        user_cfg and user_cfg.llm and (user_cfg.llm.policies or user_cfg.llm.feature_policies)
    )


def resolve_direct_role_client(
    config: ApiRoleConfig,
    user_cfg: UserConfig | None,
    feature: str,
    legacy_role: str,
) -> tuple[APIClient | None, ApiRoleConfig]:
    """Policy-aware replacement for the legacy tiered role-client resolver."""
    if user_cfg is not None and user_cfg.llm is not None and user_cfg.llm.disabled:
        return None, config
    selected = resolve_policy_client(user_cfg, feature)
    if selected.client is not None and selected.model_id is not None:
        return selected.client, config.model_copy(update={"model": selected.model_id})
    if has_explicit_policy_config(user_cfg):
        return None, config
    # Configs containing only legacy tiers/roles retain their existing behavior.
    return resolve_role_client_tiered(config, user_cfg, legacy_role)


__all__ = [
    "DirectClientResolution",
    "has_explicit_policy_config",
    "resolve_direct_role_client",
    "resolve_policy_client",
    "preview_policy",
]
