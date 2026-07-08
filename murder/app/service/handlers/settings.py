"""``settings.*`` RPC handlers."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

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
        # Dump the user llm block, masking every non-empty api_key as "***".
        if llm is None:
            return {}
        data = llm.model_dump(mode="json")
        for provider in (data.get("providers") or {}).values():
            if isinstance(provider, dict) and provider.get("api_key"):
                provider["api_key"] = "***"
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

        from murder.llm.harnesses import REGISTRY
        from murder.llm.harnesses.model_cache import get_available_models

        tui = cfg.tui
        collab_override = (
            cfg.collaborator.harness if cfg.collaborator is not None else None
        )
        planner_override = cfg.planner.harness if cfg.planner is not None else None
        live_crow = host.config.default_crow
        effective_crow = (
            list(live_crow.harnesses) if live_crow.harnesses else [live_crow.harness]
        )
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
                    **(
                        {"harnesses": list(cfg.harnesses)}
                        if cfg.harnesses
                        else {}
                    ),
                }
                for widget_id, cfg in tui.bar_widgets.items()
            },
            "default_chat_view_mode": tui.default_chat_view_mode,
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
        }

    def _settings_get(_body: dict[str, Any]) -> dict[str, Any]:
        from murder.user_config import load_user_config

        cfg = load_user_config()
        return {"ok": True, "settings": _settings_payload(cfg)}

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
                live_apply.append(
                    lambda v=value: setattr(host.config.collaborator, "harness", v)
                )

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
            existing = (
                cfg.llm.model_dump(mode="json") if cfg.llm is not None else {}
            )
            merged_llm = _deep_merge_settings(existing, incoming)
            # Resolve "***" sentinels: an incoming api_key of "***" means
            # "unchanged" — restore the stored value (empty string clears).
            stored_providers = (existing.get("providers") or {})
            for name, provider in (merged_llm.get("providers") or {}).items():
                if not isinstance(provider, dict):
                    continue
                if provider.get("api_key") == "***":
                    stored = stored_providers.get(name) or {}
                    provider["api_key"] = stored.get("api_key")
            cfg.llm = UserLlmConfig.model_validate(merged_llm)

        save_user_config(cfg)
        # Persist succeeded -> now apply the live mutations so in-memory and
        # on-disk config stay in lock-step.
        for apply in live_apply:
            apply()
        # NOTE: llm env changes are NOT applied live; they take effect at next
        # daemon start via apply_llm_env in Config.load.
        return {"ok": True, "settings": _settings_payload(cfg)}

    host.register_rpc_handler("settings.get", _settings_get)
    host.register_rpc_handler("settings.update", _settings_update)
