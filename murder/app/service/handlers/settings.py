"""``settings.*`` RPC handlers."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from murder.app.protocol.requests import CommandName, QueryName

if TYPE_CHECKING:
    from murder.app.service.host import ServiceHost


def _deep_merge_settings(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge *over* into *base*, returning a new dict.

    Nested dicts merge key-by-key; everything else (scalars, lists) is replaced.
    Used to apply a partial `llm` patch onto the stored block.
    """
    out = dict(base)
    for k, v in over.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge_settings(out[k], v)
        else:
            out[k] = v
    return out


def register(host: ServiceHost) -> None:
    def _mask_llm(llm: Any) -> dict[str, Any]:
        # Dump the user llm block, masking every non-empty API key.  Keep this
        # structural rather than tied to the legacy ``providers`` map: provider
        # instances may carry credentials at a different nesting level.
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
        # Preserve the legacy settings RPC projection while clients migrate to
        # provider-instance ``auth``/``endpoint`` fields.
        for provider in (data.get("providers") or {}).values():
            if not isinstance(provider, dict):
                continue
            auth = provider.get("auth")
            if isinstance(auth, dict) and "api_key" not in provider:
                provider["api_key"] = auth.get("api_key")
            if "base_url" not in provider and "endpoint" in provider:
                provider["base_url"] = provider.get("endpoint")
        return data

    def _crow_harnesses_override(cfg: Any) -> list[str] | None:
        # The user-scope default_crow override: harnesses pool, or [harness]
        # if only the scalar is set, else None (no override).
        crow = cfg.default_crow
        if crow is None:
            return None
        if crow.harnesses:
            return list(crow.harnesses)
        if crow.harness is not None:
            return [crow.harness]
        return None

    def _startup_rogue_payload(tui: Any) -> dict[str, Any] | None:
        sr = tui.startup_rogue
        if sr is None:
            return None
        return {"harness": sr.harness, "model": sr.model, "effort": sr.effort}

    def _settings_payload(cfg: Any) -> dict[str, Any]:
        import os as _os

        from murder.llm.clients.catalog import PROVIDER_DEFINITIONS
        from murder.llm.harnesses import REGISTRY
        from murder.llm.harnesses.model_cache import get_available_models
        from murder.user_config import BUILTIN_EXECUTION_POLICIES, UserOracleConfig

        tui = cfg.tui
        collab_override = cfg.collaborator.harness if cfg.collaborator is not None else None
        planner_override = cfg.planner.harness if cfg.planner is not None else None
        live_crow = host.config.default_crow
        effective_crow = list(live_crow.harnesses) if live_crow.harnesses else [live_crow.harness]
        return {
            # --- existing tui fields (unchanged) ---
            "theme": tui.theme,
            "modifier": tui.modifier,
            "key_overrides": dict(tui.key_overrides),
            "pane_gap": tui.pane_gap,
            "workspace_count": tui.workspace_count,
            "vim_mode": tui.vim_mode,
            "bar_widgets": {
                widget_id: {
                    "enabled": cfg.enabled,
                    "placement": cfg.placement,
                    "adaptive": cfg.adaptive,
                    **({"harnesses": list(cfg.harnesses)} if cfg.harnesses else {}),
                }
                for widget_id, cfg in tui.bar_widgets.items()
            },
            "default_chat_view_mode": tui.default_chat_view_mode,
            "document_display_mode": tui.document_display_mode,
            "startup_rogue": _startup_rogue_payload(tui),
            # --- harness overrides + effective values ---
            "collaborator_harness": collab_override,
            "planner_harness": planner_override,
            "crow_harnesses": _crow_harnesses_override(cfg),
            "effective_collaborator_harness": host.config.collaborator.harness,
            "effective_planner_harness": host.config.planner.harness,
            "effective_crow_harnesses": effective_crow,
            "startup_rogue_models": {
                harness: [
                    {"id": model_id, "label": label}
                    for model_id, label in get_available_models(harness)
                ]
                for harness in REGISTRY
            },
            "startup_rogue_efforts": {
                harness: list(adapter_cls.supported_efforts)
                for harness, adapter_cls in REGISTRY.items()
            },
            # --- llm provider/tier/role config (api keys masked) ---
            "llm": _mask_llm(cfg.llm),
            "llm_env": {
                "groq": bool(_os.environ.get("GROQ_API_KEY")),
                "cerebras": bool(_os.environ.get("CEREBRAS_API_KEY")),
                "openrouter": bool(_os.environ.get("OPENROUTER_API_KEY")),
            },
            "llm_definitions": {
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
            },
            # Execution policies and Oracle sit beside ``llm`` (§3 / §13).
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

    def _settings_get(_body: dict[str, Any]) -> dict[str, Any]:
        from murder.user_config import load_user_config

        cfg = load_user_config()
        return {"ok": True, "settings": _settings_payload(cfg)}

    def _llm_payload(cfg: Any) -> dict[str, Any]:
        """Return the persisted direct-LLM block with credentials redacted."""
        return _mask_llm(cfg.llm)

    def _load_llm_config() -> tuple[Any, Any]:
        from murder.user_config import UserLlmConfig, load_user_config

        cfg = load_user_config()
        if cfg.llm is None:
            cfg.llm = UserLlmConfig()
        return cfg, cfg.llm

    def _save_llm_config(cfg: Any) -> dict[str, Any]:
        from murder.user_config import save_user_config

        save_user_config(cfg)
        return {"ok": True, "llm": _llm_payload(cfg), "settings": _settings_payload(cfg)}

    def _provider_id(name: str, providers: dict[str, Any]) -> str:
        base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "provider"
        candidate = base
        suffix = 2
        while candidate in providers:
            candidate = f"{base}-{suffix}"
            suffix += 1
        return candidate

    def _restore_auth_sentinel(existing: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
        """Keep a masked API key when an editor submits it unchanged."""
        merged = _deep_merge_settings(existing, patch)
        auth = merged.get("auth")
        if isinstance(auth, dict) and auth.get("api_key") == "***":
            stored_auth = existing.get("auth")
            auth["api_key"] = stored_auth.get("api_key") if isinstance(stored_auth, dict) else None
        return merged

    def _llm_set_disabled(body: dict[str, Any]) -> dict[str, Any]:
        disabled = body.get("disabled")
        if not isinstance(disabled, bool):
            raise ValueError("llm.settings.set_disabled requires a boolean disabled value")
        cfg, llm = _load_llm_config()
        llm.disabled = disabled
        return _save_llm_config(cfg)

    def _llm_provider_create(body: dict[str, Any]) -> dict[str, Any]:
        from murder.user_config import UserLlmProviderSettings

        raw = body.get("provider")
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
        cfg, llm = _load_llm_config()
        provider_id = _provider_id(name, llm.providers)
        data = dict(raw)
        data["name"] = name.strip()
        data["endpoint"] = endpoint.strip()
        data["type"] = provider_type
        llm.providers[provider_id] = UserLlmProviderSettings.model_validate(data)
        reply = _save_llm_config(cfg)
        reply["provider_id"] = provider_id
        return reply

    def _llm_provider_update(body: dict[str, Any]) -> dict[str, Any]:
        from murder.user_config import UserLlmProviderSettings

        provider_id = body.get("provider_id")
        patch = body.get("patch")
        if not isinstance(provider_id, str) or not isinstance(patch, dict):
            raise ValueError("llm.provider.update requires provider_id and patch objects")
        cfg, llm = _load_llm_config()
        existing = llm.providers.get(provider_id)
        if existing is None:
            if provider_id not in {"groq", "cerebras", "openrouter", "openai", "anthropic"}:
                raise ValueError(f"unknown provider: {provider_id}")
            existing = UserLlmProviderSettings(type=provider_id, name=provider_id.title())
        data = _restore_auth_sentinel(existing.model_dump(mode="json"), patch)
        if data.get("type") != existing.type:
            raise ValueError("provider type cannot be changed")
        llm.providers[provider_id] = UserLlmProviderSettings.model_validate(data)
        return _save_llm_config(cfg)

    def _llm_provider_delete(body: dict[str, Any]) -> dict[str, Any]:
        provider_id = body.get("provider_id")
        if not isinstance(provider_id, str) or body.get("confirm") is not True:
            raise ValueError("llm.provider.delete requires provider_id and confirm=true")
        cfg, llm = _load_llm_config()
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
        return _save_llm_config(cfg)

    def _llm_provider_models_update(body: dict[str, Any]) -> dict[str, Any]:
        """Persist a model-catalog patch without allowing provider edits."""
        from murder.user_config import UserLlmModelCatalog

        provider_id = body.get("provider_id")
        patch = body.get("patch")
        if not isinstance(provider_id, str) or not isinstance(patch, dict):
            raise ValueError("llm.provider.models.update requires provider_id and patch objects")
        cfg, llm = _load_llm_config()
        provider = llm.providers.get(provider_id)
        if provider is None:
            raise ValueError(f"unknown provider: {provider_id}")
        data = _deep_merge_settings(provider.models.model_dump(mode="json"), patch)
        provider.models = UserLlmModelCatalog.model_validate(data)
        return _save_llm_config(cfg)

    async def _llm_provider_discover_models(body: dict[str, Any]) -> dict[str, Any]:
        provider_id = body.get("provider_id")
        if not isinstance(provider_id, str):
            raise ValueError("llm.provider.discover_models requires provider_id")
        cfg, llm = _load_llm_config()
        provider = llm.providers.get(provider_id)
        if provider is None:
            raise ValueError(f"unknown provider: {provider_id}")
        from murder.llm.clients.catalog import get_provider_definition

        definition = get_provider_definition(provider.type or provider_id)
        discovered = await definition.discover_models(provider)
        # Store returned IDs as catalog includes.  The resolver can therefore
        # use the discovered set before a richer runtime cache is available.
        provider.models.include = list(dict.fromkeys([*provider.models.include, *discovered]))
        from murder.user_config import save_user_config

        save_user_config(cfg)
        return {
            "ok": True,
            "models": [{"id": model_id, "label": model_id} for model_id in discovered],
        }

    def _llm_policy_create(body: dict[str, Any]) -> dict[str, Any]:
        from murder.user_config import UserLlmPolicy

        name = body.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("policy name is required")
        from murder.user_config import BUILTIN_LLM_POLICIES

        cfg, llm = _load_llm_config()
        policy_id = _provider_id(
            name, {**llm.policies, **{k: None for k in BUILTIN_LLM_POLICIES}}
        )
        raw = body.get("policy")
        data = raw if isinstance(raw, dict) else {}
        data = {**data, "builtin": False, "name": name.strip()}
        llm.policies[policy_id] = UserLlmPolicy.model_validate(data)
        reply = _save_llm_config(cfg)
        reply["policy_id"] = policy_id
        return reply

    def _llm_policy_update(body: dict[str, Any]) -> dict[str, Any]:
        from murder.user_config import UserLlmPolicy

        policy_id = body.get("policy_id")
        patch = body.get("patch")
        if not isinstance(policy_id, str) or not isinstance(patch, dict):
            raise ValueError("llm.policy.update requires policy_id and patch objects")
        cfg, llm = _load_llm_config()
        policy = llm.policies.get(policy_id)
        if policy is None:
            raise ValueError(
                "built-in policies are immutable"
                if llm.resolved_policy(policy_id)
                else f"unknown policy: {policy_id}"
            )
        data = _deep_merge_settings(policy.model_dump(mode="json"), patch)
        data["builtin"] = False
        llm.policies[policy_id] = UserLlmPolicy.model_validate(data)
        return _save_llm_config(cfg)

    def _llm_policy_delete(body: dict[str, Any]) -> dict[str, Any]:
        policy_id = body.get("policy_id")
        if not isinstance(policy_id, str) or body.get("confirm") is not True:
            raise ValueError("llm.policy.delete requires policy_id and confirm=true")
        cfg, llm = _load_llm_config()
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
        return _save_llm_config(cfg)

    def _llm_policy_activate(body: dict[str, Any]) -> dict[str, Any]:
        policy_id = body.get("policy_id")
        if not isinstance(policy_id, str):
            raise ValueError("llm.policy.activate requires policy_id")
        cfg, llm = _load_llm_config()
        if llm.resolved_policy(policy_id) is None:
            raise ValueError(f"unknown policy: {policy_id}")
        llm.active_policy = policy_id
        return _save_llm_config(cfg)

    def _llm_policy_clone(body: dict[str, Any]) -> dict[str, Any]:
        from murder.user_config import BUILTIN_LLM_POLICIES, UserLlmPolicy

        source_id = body.get("policy_id")
        name = body.get("name")
        if not isinstance(source_id, str) or not isinstance(name, str) or not name.strip():
            raise ValueError("llm.policy.clone requires policy_id and a name")
        cfg, llm = _load_llm_config()
        source = llm.resolved_policy(source_id)
        if source is None:
            raise ValueError(f"unknown policy: {source_id}")
        policy_id = _provider_id(
            name,
            {**llm.policies, **{k: None for k in BUILTIN_LLM_POLICIES}},
        )
        data = source.model_dump(mode="json")
        data.update({"builtin": False, "name": name.strip()})
        llm.policies[policy_id] = UserLlmPolicy.model_validate(data)
        reply = _save_llm_config(cfg)
        reply["policy_id"] = policy_id
        return reply

    def _llm_feature_policy_set(body: dict[str, Any]) -> dict[str, Any]:
        feature_type = body.get("feature_type")
        policy_id = body.get("policy_id")
        if not isinstance(feature_type, str) or not feature_type.strip():
            raise ValueError("llm.feature_policy.set requires feature_type")
        if policy_id is not None and not isinstance(policy_id, str):
            raise ValueError("policy_id must be a string, 'disabled', or null")
        cfg, llm = _load_llm_config()
        if policy_id is None:
            llm.feature_policies.pop(feature_type, None)
        elif policy_id != "disabled":
            if llm.resolved_policy(policy_id) is None:
                raise ValueError(f"unknown policy: {policy_id}")
            llm.feature_policies[feature_type] = policy_id
        else:
            llm.feature_policies[feature_type] = "disabled"
        return _save_llm_config(cfg)

    def _llm_preview_resolution(body: dict[str, Any]) -> dict[str, Any]:
        """Resolve candidates for the UI without constructing a provider client."""
        from murder.llm.direct import preview_policy
        from murder.llm.policy import InferenceRequirements

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
        cfg, _llm = _load_llm_config()
        resolution = preview_policy(
            cfg,
            feature_type,
            requirements=InferenceRequirements(
                feature_type=feature_type,
                required_capabilities=frozenset(capabilities),
                required_execution_mode=execution_mode,
                min_context_tokens=min_context_tokens,
            )
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

    def _settings_update(body: dict[str, Any]) -> dict[str, Any]:
        # Partial merge: load the persisted user config, overlay only the provided keys,
        # re-validate via pydantic, and persist. We call load/save directly rather than
        # SettingsService.save_global to avoid its model-discovery side effects.
        from typing import get_args

        from murder.user_config import (
            BarWidgetUserConfig,
            TuiUserConfig,
            UserHarnessKind,
            UserHarnessRolePatch,
            UserLlmConfig,
            load_user_config,
            save_user_config,
        )

        partial = body.get("settings")
        if not isinstance(partial, dict):
            raise ValueError("settings.update requires a settings object")

        cfg = load_user_config()
        # Live-apply mutations are deferred until AFTER save_user_config
        # succeeds, so a failed persist (disk full, validation) doesn't leave
        # the in-memory config diverged from the file. Persist first, then
        # apply.
        live_apply: list[Callable[[], None]] = []

        valid_harnesses = set(get_args(UserHarnessKind))

        # --- tui keys (re-validate the merged tui block) ---
        tui_merged: dict[str, Any] = {
            "theme": cfg.tui.theme,
            "modifier": cfg.tui.modifier,
            "key_overrides": dict(cfg.tui.key_overrides),
            "pane_gap": cfg.tui.pane_gap,
            "workspace_count": cfg.tui.workspace_count,
            "vim_mode": cfg.tui.vim_mode,
            "default_chat_view_mode": cfg.tui.default_chat_view_mode,
            "document_display_mode": cfg.tui.document_display_mode,
            "startup_rogue": (
                cfg.tui.startup_rogue.model_dump(mode="json")
                if cfg.tui.startup_rogue is not None
                else None
            ),
            "bar_widgets": {
                widget_id: cfg.model_dump(mode="json")
                for widget_id, cfg in cfg.tui.bar_widgets.items()
            },
        }
        for key in (
            "theme",
            "modifier",
            "key_overrides",
            "pane_gap",
            "workspace_count",
            "vim_mode",
            "default_chat_view_mode",
            "document_display_mode",
        ):
            if key in partial:
                tui_merged[key] = partial[key]
        if "bar_widgets" in partial:
            incoming = partial["bar_widgets"]
            if not isinstance(incoming, dict):
                raise ValueError("bar_widgets must be an object")
            merged_widgets = {
                widget_id: cfg.model_dump(mode="json")
                for widget_id, cfg in cfg.tui.bar_widgets.items()
            }
            for widget_id, patch in incoming.items():
                if not isinstance(widget_id, str) or not isinstance(patch, dict):
                    raise ValueError("bar_widgets entries must be {id: {enabled, placement}}")
                base = merged_widgets.get(widget_id, BarWidgetUserConfig().model_dump(mode="json"))
                merged_patch = {**base, **patch}
                if "harnesses" in patch:
                    harnesses_val = patch["harnesses"]
                    if harnesses_val is None:
                        merged_patch["harnesses"] = None
                    elif isinstance(harnesses_val, list):
                        for h in harnesses_val:
                            if h not in valid_harnesses:
                                raise ValueError(f"invalid bar widget harness: {h!r}")
                        merged_patch["harnesses"] = harnesses_val or None
                    else:
                        raise ValueError("bar_widgets harnesses must be a list or null")
                merged_widgets[widget_id] = merged_patch
            tui_merged["bar_widgets"] = {
                widget_id: BarWidgetUserConfig.model_validate(values)
                for widget_id, values in merged_widgets.items()
            }
        # startup_rogue: null clears it; an object sets harness/model/effort (validated here so a
        # bad harness is rejected before persist). The merged dict re-validates via TuiUserConfig.
        if "startup_rogue" in partial:
            sr_val = partial["startup_rogue"]
            if sr_val is None:
                tui_merged["startup_rogue"] = None
            elif isinstance(sr_val, dict):
                harness = sr_val.get("harness")
                if harness not in valid_harnesses:
                    raise ValueError(f"invalid startup_rogue harness: {harness!r}")
                effort = sr_val.get("effort")
                if effort is not None and not isinstance(effort, str):
                    raise ValueError("startup_rogue effort must be a string or null")
                tui_merged["startup_rogue"] = {
                    "harness": harness,
                    "model": str(sr_val.get("model") or ""),
                    "effort": effort if (isinstance(effort, str) and effort.strip()) else None,
                }
            else:
                raise ValueError("startup_rogue must be an object or null")
        cfg.tui = TuiUserConfig.model_validate(tui_merged)

        # --- collaborator_harness override ---
        if "collaborator_harness" in partial:
            value = partial["collaborator_harness"]
            if value is None:
                if cfg.collaborator is not None:
                    cfg.collaborator.harness = None
            else:
                if value not in valid_harnesses:
                    raise ValueError(f"invalid collaborator harness: {value!r}")
                patch = cfg.collaborator or UserHarnessRolePatch()
                patch.harness = value
                cfg.collaborator = patch
                # Apply live so new spawns use it without a daemon restart.
                live_apply.append(lambda v=value: setattr(host.config.collaborator, "harness", v))

        # --- planner_harness override ---
        if "planner_harness" in partial:
            value = partial["planner_harness"]
            if value is None:
                if cfg.planner is not None:
                    cfg.planner.harness = None
            else:
                if value not in valid_harnesses:
                    raise ValueError(f"invalid planner harness: {value!r}")
                patch = cfg.planner or UserHarnessRolePatch()
                patch.harness = value
                cfg.planner = patch
                # Apply live so newly spawned planning agents use it without a daemon restart.
                live_apply.append(lambda v=value: setattr(host.config.planner, "harness", v))

        # --- crow_harnesses override (single -> harness; multi -> harnesses; null -> clear) ---
        if "crow_harnesses" in partial:
            value = partial["crow_harnesses"]
            if value is None:
                if cfg.default_crow is not None:
                    cfg.default_crow.harness = None
                    cfg.default_crow.harnesses = None
            else:
                if not isinstance(value, list) or not value:
                    raise ValueError("crow_harnesses must be a non-empty list or null")
                for h in value:
                    if h not in valid_harnesses:
                        raise ValueError(f"invalid crow harness: {h!r}")
                patch = cfg.default_crow or UserHarnessRolePatch()
                if len(value) == 1:
                    patch.harness = value[0]
                    patch.harnesses = None
                else:
                    patch.harness = value[0]
                    patch.harnesses = list(value)
                cfg.default_crow = patch
                # Apply live so new spawns use it without a daemon restart.
                _live_harness = value[0]
                _live_harnesses = list(value) if len(value) > 1 else None

                def _apply_crow(h=_live_harness, hs=_live_harnesses) -> None:
                    host.config.default_crow.harness = h
                    host.config.default_crow.harnesses = hs

                live_apply.append(_apply_crow)

        # --- llm block (deep-merge; "***" api_key sentinel = keep stored value) ---
        if "llm" in partial:
            incoming = partial["llm"]
            if not isinstance(incoming, dict):
                raise ValueError("llm must be an object")
            # Translate the legacy flat credential fields before merging the
            # typed provider-instance config.  Existing TUI clients use these
            # names; new clients send ``auth.api_key`` and ``endpoint``.
            incoming = _deep_merge_settings({}, incoming)
            providers = incoming.get("providers")
            if isinstance(providers, dict):
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
            existing = cfg.llm.model_dump(mode="json") if cfg.llm is not None else {}
            merged_llm = _deep_merge_settings(existing, incoming)
            # Resolve "***" sentinels: an incoming api_key of "***" means
            # "unchanged" — restore the stored value (empty string clears).
            stored_providers = existing.get("providers") or {}
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
            cfg.llm = UserLlmConfig.model_validate(merged_llm)

        if "execution" in partial:
            from murder.user_config import UserExecutionConfig

            incoming = partial["execution"]
            if not isinstance(incoming, dict):
                raise ValueError("execution must be an object")
            existing_exec = (
                cfg.execution.model_dump(mode="json") if cfg.execution is not None else {}
            )
            cfg.execution = UserExecutionConfig.model_validate(
                _deep_merge_settings(existing_exec, incoming)
            )

        if "oracle" in partial:
            from murder.user_config import UserOracleConfig

            incoming = partial["oracle"]
            if not isinstance(incoming, dict):
                raise ValueError("oracle must be an object")
            existing_oracle = (
                cfg.oracle.model_dump(mode="json") if cfg.oracle is not None else {}
            )
            cfg.oracle = UserOracleConfig.model_validate(
                _deep_merge_settings(existing_oracle, incoming)
            )

        save_user_config(cfg)
        # Persist succeeded -> now apply the live mutations so in-memory and
        # on-disk config stay in lock-step.
        for apply in live_apply:
            apply()
        # NOTE: llm env changes are NOT applied live; they take effect at next
        # daemon start via apply_llm_env in Config.load.
        return {"ok": True, "settings": _settings_payload(cfg)}

    host.register_application_query(QueryName.SETTINGS_GET, _settings_get)
    host.register_application_command(CommandName.SETTINGS_UPDATE, _settings_update)
    host.register_application_command(CommandName.LLM_SETTINGS_SET_DISABLED, _llm_set_disabled)
    host.register_application_command(CommandName.LLM_PROVIDER_CREATE, _llm_provider_create)
    host.register_application_command(CommandName.LLM_PROVIDER_UPDATE, _llm_provider_update)
    host.register_application_command(CommandName.LLM_PROVIDER_DELETE, _llm_provider_delete)
    host.register_application_command(
        CommandName.LLM_PROVIDER_MODELS_UPDATE, _llm_provider_models_update
    )
    host.register_application_command(
        CommandName.LLM_PROVIDER_DISCOVER_MODELS, _llm_provider_discover_models
    )
    host.register_application_command(CommandName.LLM_POLICY_CREATE, _llm_policy_create)
    host.register_application_command(CommandName.LLM_POLICY_UPDATE, _llm_policy_update)
    host.register_application_command(CommandName.LLM_POLICY_DELETE, _llm_policy_delete)
    host.register_application_command(CommandName.LLM_POLICY_ACTIVATE, _llm_policy_activate)
    host.register_application_command(CommandName.LLM_POLICY_CLONE, _llm_policy_clone)
    host.register_application_command(
        CommandName.LLM_FEATURE_POLICY_SET, _llm_feature_policy_set
    )
    host.register_application_command(CommandName.LLM_PREVIEW_RESOLUTION, _llm_preview_resolution)
