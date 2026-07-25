"""LLM provider/policy/discovery/preview use cases."""

from __future__ import annotations

import re
from typing import Any

from murder.app.service.settings.ports import SettingsRepository
from murder.app.service.settings.view import (
    EffectiveHarnessView,
    build_settings_payload,
    deep_merge_settings,
    mask_llm,
    restore_auth_sentinel,
)
from murder.llm.clients.catalog import get_provider_definition
from murder.llm.direct import preview_policy
from murder.llm.policy import InferenceRequirements
from murder.user_config import (
    BUILTIN_LLM_POLICIES,
    UserConfig,
    UserLlmConfig,
    UserLlmModelCatalog,
    UserLlmPolicy,
    UserLlmProviderSettings,
)


def ensure_llm_config(cfg: UserConfig) -> UserLlmConfig:
    if cfg.llm is None:
        cfg.llm = UserLlmConfig()
    return cfg.llm


def llm_reply(
    cfg: UserConfig,
    *,
    effective: EffectiveHarnessView,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    reply: dict[str, Any] = {
        "ok": True,
        "llm": mask_llm(cfg.llm),
        "settings": build_settings_payload(cfg, effective=effective),
    }
    if extra:
        reply.update(extra)
    return reply


def allocate_id(name: str, existing: dict[str, Any]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "provider"
    candidate = base
    suffix = 2
    while candidate in existing:
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def set_disabled(cfg: UserConfig, disabled: Any) -> UserConfig:
    if not isinstance(disabled, bool):
        raise ValueError("llm.settings.set_disabled requires a boolean disabled value")
    llm = ensure_llm_config(cfg)
    llm.disabled = disabled
    return cfg


def create_provider(cfg: UserConfig, raw: Any) -> tuple[UserConfig, str]:
    if not isinstance(raw, dict):
        raise ValueError("llm.provider.create requires a provider object")
    provider_type = raw.get("type")
    if provider_type not in {"openai_compatible", "lemonade"}:
        raise ValueError("only OpenAI-compatible and Lemonade providers may be created")
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("provider name is required")
    endpoint = raw.get("endpoint")
    if not isinstance(endpoint, str) or not endpoint.strip():
        raise ValueError("custom provider endpoint is required")
    llm = ensure_llm_config(cfg)
    provider_id = allocate_id(name, llm.providers)
    data = dict(raw)
    data["name"] = name.strip()
    data["endpoint"] = endpoint.strip()
    data["type"] = provider_type
    llm.providers[provider_id] = UserLlmProviderSettings.model_validate(data)
    return cfg, provider_id


def update_provider(cfg: UserConfig, provider_id: Any, patch: Any) -> UserConfig:
    if not isinstance(provider_id, str) or not isinstance(patch, dict):
        raise ValueError("llm.provider.update requires provider_id and patch objects")
    llm = ensure_llm_config(cfg)
    existing = llm.providers.get(provider_id)
    if existing is None:
        if provider_id not in {"groq", "cerebras", "openrouter", "openai", "anthropic"}:
            raise ValueError(f"unknown provider: {provider_id}")
        existing = UserLlmProviderSettings(type=provider_id, name=provider_id.title())
    data = restore_auth_sentinel(existing.model_dump(mode="json"), patch)
    if data.get("type") != existing.type:
        raise ValueError("provider type cannot be changed")
    llm.providers[provider_id] = UserLlmProviderSettings.model_validate(data)
    return cfg


def delete_provider(cfg: UserConfig, provider_id: Any, *, confirm: Any) -> UserConfig:
    if not isinstance(provider_id, str) or confirm is not True:
        raise ValueError("llm.provider.delete requires provider_id and confirm=true")
    llm = ensure_llm_config(cfg)
    provider = llm.providers.get(provider_id)
    if provider is None:
        raise ValueError(f"unknown provider: {provider_id}")
    if provider.type not in {"openai_compatible", "lemonade"}:
        raise ValueError("built-in providers cannot be deleted")
    references = [
        policy_id
        for policy_id, policy in llm.policies.items()
        if any(
            selector.candidate is not None and selector.candidate.provider == provider_id
            for group in policy.groups
            for selector in group.selectors
        )
    ]
    if references:
        raise ValueError(f"provider is referenced by policies: {', '.join(references)}")
    del llm.providers[provider_id]
    return cfg


def update_provider_models(cfg: UserConfig, provider_id: Any, patch: Any) -> UserConfig:
    if not isinstance(provider_id, str) or not isinstance(patch, dict):
        raise ValueError("llm.provider.models.update requires provider_id and patch objects")
    llm = ensure_llm_config(cfg)
    provider = llm.providers.get(provider_id)
    if provider is None:
        raise ValueError(f"unknown provider: {provider_id}")
    data = deep_merge_settings(provider.models.model_dump(mode="json"), patch)
    provider.models = UserLlmModelCatalog.model_validate(data)
    return cfg


async def discover_provider_models(
    cfg: UserConfig, provider_id: Any
) -> tuple[UserConfig, list[str]]:
    if not isinstance(provider_id, str):
        raise ValueError("llm.provider.discover_models requires provider_id")
    llm = ensure_llm_config(cfg)
    provider = llm.providers.get(provider_id)
    if provider is None:
        raise ValueError(f"unknown provider: {provider_id}")
    definition = get_provider_definition(provider.type or provider_id)
    discovered = await definition.discover_models(provider)
    provider.models.include = list(dict.fromkeys([*provider.models.include, *discovered]))
    return cfg, discovered


def create_policy(cfg: UserConfig, name: Any, policy: Any) -> tuple[UserConfig, str]:
    if not isinstance(name, str) or not name.strip():
        raise ValueError("policy name is required")
    llm = ensure_llm_config(cfg)
    policy_id = allocate_id(name, {**llm.policies, **{k: None for k in BUILTIN_LLM_POLICIES}})
    data = policy if isinstance(policy, dict) else {}
    data = {**data, "builtin": False, "name": name.strip()}
    llm.policies[policy_id] = UserLlmPolicy.model_validate(data)
    return cfg, policy_id


def update_policy(cfg: UserConfig, policy_id: Any, patch: Any) -> UserConfig:
    if not isinstance(policy_id, str) or not isinstance(patch, dict):
        raise ValueError("llm.policy.update requires policy_id and patch objects")
    llm = ensure_llm_config(cfg)
    policy = llm.policies.get(policy_id)
    if policy is None:
        raise ValueError(
            "built-in policies are immutable"
            if llm.resolved_policy(policy_id)
            else f"unknown policy: {policy_id}"
        )
    data = deep_merge_settings(policy.model_dump(mode="json"), patch)
    data["builtin"] = False
    llm.policies[policy_id] = UserLlmPolicy.model_validate(data)
    return cfg


def delete_policy(cfg: UserConfig, policy_id: Any, *, confirm: Any) -> UserConfig:
    if not isinstance(policy_id, str) or confirm is not True:
        raise ValueError("llm.policy.delete requires policy_id and confirm=true")
    llm = ensure_llm_config(cfg)
    if policy_id not in llm.policies:
        raise ValueError(
            "built-in policies cannot be deleted"
            if llm.resolved_policy(policy_id)
            else f"unknown policy: {policy_id}"
        )
    references: list[str] = []
    if llm.active_policy == policy_id:
        references.append("active policy")
    references.extend(
        f"feature:{feature_type}"
        for feature_type, assigned_policy in llm.feature_policies.items()
        if assigned_policy == policy_id
    )
    if references:
        raise ValueError(f"policy is referenced by: {', '.join(references)}")
    del llm.policies[policy_id]
    return cfg


def activate_policy(cfg: UserConfig, policy_id: Any) -> UserConfig:
    if not isinstance(policy_id, str):
        raise ValueError("llm.policy.activate requires policy_id")
    llm = ensure_llm_config(cfg)
    if llm.resolved_policy(policy_id) is None:
        raise ValueError(f"unknown policy: {policy_id}")
    llm.active_policy = policy_id
    return cfg


def clone_policy(cfg: UserConfig, source_id: Any, name: Any) -> tuple[UserConfig, str]:
    if not isinstance(source_id, str) or not isinstance(name, str) or not name.strip():
        raise ValueError("llm.policy.clone requires policy_id and a name")
    llm = ensure_llm_config(cfg)
    source = llm.resolved_policy(source_id)
    if source is None:
        raise ValueError(f"unknown policy: {source_id}")
    policy_id = allocate_id(
        name,
        {**llm.policies, **{k: None for k in BUILTIN_LLM_POLICIES}},
    )
    data = source.model_dump(mode="json")
    data.update({"builtin": False, "name": name.strip()})
    llm.policies[policy_id] = UserLlmPolicy.model_validate(data)
    return cfg, policy_id


def set_feature_policy(cfg: UserConfig, feature_type: Any, policy_id: Any) -> UserConfig:
    if not isinstance(feature_type, str) or not feature_type.strip():
        raise ValueError("llm.feature_policy.set requires feature_type")
    if policy_id is not None and not isinstance(policy_id, str):
        raise ValueError("policy_id must be a string, 'disabled', or null")
    llm = ensure_llm_config(cfg)
    if policy_id is None:
        llm.feature_policies.pop(feature_type, None)
    elif policy_id != "disabled":
        if llm.resolved_policy(policy_id) is None:
            raise ValueError(f"unknown policy: {policy_id}")
        llm.feature_policies[feature_type] = policy_id
    else:
        llm.feature_policies[feature_type] = "disabled"
    return cfg


def preview_resolution(cfg: UserConfig, body: dict[str, Any]) -> dict[str, Any]:
    feature_type = body.get("feature_type")
    if not isinstance(feature_type, str) or not feature_type.strip():
        raise ValueError("llm.preview_resolution requires feature_type")
    capabilities = body.get("required_capabilities", [])
    if not isinstance(capabilities, list) or not all(
        isinstance(item, str) for item in capabilities
    ):
        raise ValueError("required_capabilities must be a list of strings")
    execution_mode = body.get("required_execution_mode")
    if execution_mode is not None and not isinstance(execution_mode, str):
        raise ValueError("required_execution_mode must be a string or null")
    min_context_tokens = body.get("min_context_tokens")
    if min_context_tokens is not None and (
        not isinstance(min_context_tokens, int) or min_context_tokens < 1
    ):
        raise ValueError("min_context_tokens must be a positive integer or null")
    ensure_llm_config(cfg)
    resolution = preview_policy(
        cfg,
        feature_type,
        requirements=InferenceRequirements(
            feature_type=feature_type,
            required_capabilities=frozenset(capabilities),
            required_execution_mode=execution_mode,
            min_context_tokens=min_context_tokens,
        ),
    )
    return {
        "ok": True,
        "status": resolution.status,
        "policy_id": resolution.policy_name,
        "candidates": [
            {
                "provider_id": candidate.provider_id,
                "provider_type": candidate.provider_type,
                "model_id": candidate.model_id,
                "locality": candidate.metadata.locality,
                "cost_class": candidate.metadata.cost_class,
            }
            for candidate in resolution.candidates
        ],
    }


def load_mutable_config(repository: SettingsRepository) -> UserConfig:
    cfg = repository.load()
    ensure_llm_config(cfg)
    return cfg


__all__ = [
    "activate_policy",
    "allocate_id",
    "clone_policy",
    "create_policy",
    "create_provider",
    "delete_policy",
    "delete_provider",
    "discover_provider_models",
    "ensure_llm_config",
    "llm_reply",
    "load_mutable_config",
    "preview_resolution",
    "set_disabled",
    "set_feature_policy",
    "update_policy",
    "update_provider",
    "update_provider_models",
]
