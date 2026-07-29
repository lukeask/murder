"""Settings response projection: redaction, compatibility aliases, payload shape."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from murder.config import Config
from murder.llm.clients.catalog import PROVIDER_DEFINITIONS
from murder.llm.harnesses import REGISTRY
from murder.llm.harnesses.model_cache import get_available_models
from murder.user_config import BUILTIN_EXECUTION_POLICIES, UserConfig, UserOracleConfig


def deep_merge_settings(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge *over* into *base*, returning a new dict.

    Nested dicts merge key-by-key; everything else (scalars, lists) is replaced.
    """
    out = dict(base)
    for key, value in over.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = deep_merge_settings(out[key], value)
        else:
            out[key] = value
    return out


def mask_llm(llm: Any) -> dict[str, Any]:
    """Dump the user llm block, masking every non-empty API key.

    Also projects legacy ``api_key`` / ``base_url`` aliases from provider-instance
    ``auth`` / ``endpoint`` fields so older clients keep working.
    """
    if llm is None:
        return {}
    data = llm.model_dump(mode="json")

    def _mask(value: Any) -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                if key == "api_key" and nested:
                    value[key] = "***"
                else:
                    _mask(nested)
        elif isinstance(value, list):
            for nested in value:
                _mask(nested)

    _mask(data)
    for provider in (data.get("providers") or {}).values():
        if not isinstance(provider, dict):
            continue
        auth = provider.get("auth")
        if isinstance(auth, dict) and "api_key" not in provider:
            provider["api_key"] = auth.get("api_key")
        if "base_url" not in provider and "endpoint" in provider:
            provider["base_url"] = provider.get("endpoint")
    return data


def crow_harnesses_override(cfg: UserConfig) -> list[str] | None:
    """User-scope default_crow override: harnesses pool, or [harness], else None."""
    crow = cfg.default_crow
    if crow is None:
        return None
    if crow.harnesses:
        return list(crow.harnesses)
    if crow.harness is not None:
        return [crow.harness]
    return None


def startup_rogue_payload(tui: Any) -> dict[str, Any] | None:
    sr = tui.startup_rogue
    if sr is None:
        return None
    return {"harness": sr.harness, "model": sr.model, "effort": sr.effort}


def translate_legacy_llm_provider_fields(incoming: dict[str, Any]) -> dict[str, Any]:
    """Map flat ``api_key``/``base_url`` onto provider ``auth``/``endpoint``."""
    incoming = deep_merge_settings({}, incoming)
    providers = incoming.get("providers")
    if not isinstance(providers, dict):
        return incoming
    for provider in providers.values():
        if not isinstance(provider, dict):
            continue
        if "api_key" in provider:
            auth = provider.get("auth")
            auth = dict(auth) if isinstance(auth, dict) else {}
            auth["api_key"] = provider.pop("api_key")
            provider["auth"] = auth
        if "base_url" in provider:
            provider["endpoint"] = provider.pop("base_url")
    return incoming


def restore_api_key_sentinels(
    merged_llm: dict[str, Any],
    stored_providers: Mapping[str, Any],
) -> dict[str, Any]:
    """Resolve ``\"***\"`` api_key sentinels back to the stored secret."""
    for name, provider in (merged_llm.get("providers") or {}).items():
        if not isinstance(provider, dict):
            continue
        auth = provider.get("auth")
        if isinstance(auth, dict) and auth.get("api_key") == "***":
            stored = stored_providers.get(name) or {}
            stored_auth = stored.get("auth") if isinstance(stored, dict) else None
            auth["api_key"] = (
                stored_auth.get("api_key") if isinstance(stored_auth, dict) else None
            )
    return merged_llm


def restore_auth_sentinel(existing: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Keep a masked API key when an editor submits it unchanged."""
    merged = deep_merge_settings(existing, patch)
    auth = merged.get("auth")
    if isinstance(auth, dict) and auth.get("api_key") == "***":
        stored_auth = existing.get("auth")
        auth["api_key"] = stored_auth.get("api_key") if isinstance(stored_auth, dict) else None
    return merged


@dataclass(frozen=True, slots=True)
class EffectiveHarnessView:
    collaborator: str
    planner: str
    crow: tuple[str, ...]


def effective_harness_view(live: Config) -> EffectiveHarnessView:
    """Project the running ``Config`` role selection into the settings payload view."""
    crow = live.default_crow
    effective_crow = list(crow.harnesses) if crow.harnesses else [crow.harness]
    return EffectiveHarnessView(
        collaborator=live.collaborator.harness,
        planner=live.planner.harness,
        crow=tuple(effective_crow),
    )


def default_llm_env() -> dict[str, bool]:
    return {
        "groq": bool(os.environ.get("GROQ_API_KEY")),
        "cerebras": bool(os.environ.get("CEREBRAS_API_KEY")),
        "openrouter": bool(os.environ.get("OPENROUTER_API_KEY")),
        "openai": bool(os.environ.get("OPENAI_API_KEY")),
        "anthropic": bool(os.environ.get("ANTHROPIC_API_KEY")),
    }


def build_llm_definitions() -> dict[str, Any]:
    return {
        provider_type: {
            "label": definition.label,
            "default_endpoint": definition.default_endpoint,
            "canonical_instance": definition.canonical_instance,
            "multiple_instances": definition.multiple_instances,
            "supports_discovery": definition.supports_discovery,
            "execution_modes": sorted(definition.metadata.execution_modes),
            "fields": [
                {
                    "name": field.name,
                    "label": field.label,
                    "kind": field.kind,
                    "required": field.required,
                    "secret": field.secret,
                }
                for field in definition.field_specs
            ],
            "presets": [
                {
                    "id": preset.id,
                    "label": preset.label,
                    "execution_modes": sorted(
                        (definition.metadata.execution_modes | preset.metadata.execution_modes)
                        or frozenset({"immediate"})
                    ),
                }
                for preset in definition.presets
            ],
        }
        for provider_type, definition in PROVIDER_DEFINITIONS.items()
    }


def build_startup_rogue_catalogs() -> tuple[dict[str, Any], dict[str, Any]]:
    models = {
        harness: [
            {"id": model_id, "label": label}
            for model_id, label in get_available_models(harness)
        ]
        for harness in REGISTRY
    }
    efforts = {
        harness: list(adapter_cls.supported_efforts)
        for harness, adapter_cls in REGISTRY.items()
    }
    return models, efforts


def build_settings_payload(
    cfg: UserConfig,
    *,
    effective: EffectiveHarnessView,
    llm_env: Mapping[str, bool] | None = None,
) -> dict[str, Any]:
    """Project persisted + effective settings for ``settings.get`` / update replies."""
    tui = cfg.tui
    collab_override = cfg.collaborator.harness if cfg.collaborator is not None else None
    planner_override = cfg.planner.harness if cfg.planner is not None else None
    startup_models, startup_efforts = build_startup_rogue_catalogs()
    return {
        "theme": tui.theme,
        "modifier": tui.modifier,
        "key_overrides": dict(tui.key_overrides),
        "pane_gap": tui.pane_gap,
        "background_transparency": tui.background_transparency,
        "workspace_count": tui.workspace_count,
        "vim_mode": tui.vim_mode,
        "bar_widgets": {
            widget_id: {
                "enabled": widget.enabled,
                "placement": widget.placement,
                "adaptive": widget.adaptive,
                **({"harnesses": list(widget.harnesses)} if widget.harnesses else {}),
            }
            for widget_id, widget in tui.bar_widgets.items()
        },
        "default_chat_view_mode": tui.default_chat_view_mode,
        "document_display_mode": tui.document_display_mode,
        "codex_control_backend": tui.codex_control_backend,
        "cursor_control_backend": tui.cursor_control_backend,
        "claude_control_backend": tui.claude_control_backend,
        "startup_rogue": startup_rogue_payload(tui),
        "collaborator_harness": collab_override,
        "planner_harness": planner_override,
        "crow_harnesses": crow_harnesses_override(cfg),
        "effective_collaborator_harness": effective.collaborator,
        "effective_planner_harness": effective.planner,
        "effective_crow_harnesses": list(effective.crow),
        "startup_rogue_models": startup_models,
        "startup_rogue_efforts": startup_efforts,
        "llm": mask_llm(cfg.llm),
        "llm_env": dict(llm_env) if llm_env is not None else default_llm_env(),
        "llm_definitions": build_llm_definitions(),
        "execution": (
            cfg.execution.model_dump(mode="json")
            if cfg.execution is not None
            else {"policies": {}}
        ),
        "execution_definitions": {
            policy_id: policy.model_dump(mode="json")
            for policy_id, policy in BUILTIN_EXECUTION_POLICIES.items()
        },
        "oracle": (
            cfg.oracle.model_dump(mode="json")
            if cfg.oracle is not None
            else UserOracleConfig().model_dump(mode="json")
        ),
    }


__all__ = [
    "EffectiveHarnessView",
    "build_settings_payload",
    "crow_harnesses_override",
    "deep_merge_settings",
    "default_llm_env",
    "effective_harness_view",
    "mask_llm",
    "restore_api_key_sentinels",
    "restore_auth_sentinel",
    "startup_rogue_payload",
    "translate_legacy_llm_provider_fields",
]
