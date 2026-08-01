"""Pure settings-patch semantics → ``SettingsMutation``."""

from __future__ import annotations

from typing import Any, get_args

from murder.app.service.settings.ports import (
    ClearCollaboratorHarness,
    ClearCrowHarnesses,
    ClearPlannerHarness,
    LiveChange,
    SetCollaboratorHarness,
    SetCrowHarnesses,
    SetPlannerHarness,
    SettingsMutation,
)
from murder.app.service.settings.view import (
    deep_merge_settings,
    restore_api_key_sentinels,
    translate_legacy_llm_provider_fields,
)
from murder.user_config import (
    BarWidgetUserConfig,
    ClaudeControlBackend,
    CodexControlBackend,
    CursorControlBackend,
    TuiUserConfig,
    UserConfig,
    UserExecutionConfig,
    UserHarnessKind,
    UserHarnessRolePatch,
    UserLlmConfig,
    UserOracleConfig,
)

_TUI_SCALAR_KEYS = (
    "theme",
    "modifier",
    "key_overrides",
    "pane_gap",
    "background_transparency",
    "workspace_count",
    "vim_mode",
    "default_chat_view_mode",
    "document_display_mode",
)


def _require_backend(value: Any, backend_type: type[Any], field: str) -> Any:
    valid = set(get_args(backend_type))
    if value not in valid:
        raise ValueError(f"{field} must be one of {sorted(valid)}. Got {value!r}.")
    return value


def _tui_base(cfg: UserConfig) -> dict[str, Any]:
    return {
        "theme": cfg.tui.theme,
        "modifier": cfg.tui.modifier,
        "key_overrides": dict(cfg.tui.key_overrides),
        "pane_gap": cfg.tui.pane_gap,
        "background_transparency": cfg.tui.background_transparency,
        "workspace_count": cfg.tui.workspace_count,
        "vim_mode": cfg.tui.vim_mode,
        "default_chat_view_mode": cfg.tui.default_chat_view_mode,
        "document_display_mode": cfg.tui.document_display_mode,
        "codex_control_backend": cfg.tui.codex_control_backend,
        "cursor_control_backend": cfg.tui.cursor_control_backend,
        "claude_control_backend": cfg.tui.claude_control_backend,
        "startup_rogue": (
            cfg.tui.startup_rogue.model_dump(mode="json")
            if cfg.tui.startup_rogue is not None
            else None
        ),
        "bar_widgets": {
            widget_id: widget.model_dump(mode="json")
            for widget_id, widget in cfg.tui.bar_widgets.items()
        },
    }


def _patch_bar_widgets(
    cfg: UserConfig,
    incoming: Any,
    valid_harnesses: set[str],
) -> dict[str, BarWidgetUserConfig]:
    if not isinstance(incoming, dict):
        raise ValueError("bar_widgets must be an object")
    merged_widgets = {
        widget_id: widget.model_dump(mode="json")
        for widget_id, widget in cfg.tui.bar_widgets.items()
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
                for harness in harnesses_val:
                    if harness not in valid_harnesses:
                        raise ValueError(f"invalid bar widget harness: {harness!r}")
                merged_patch["harnesses"] = harnesses_val or None
            else:
                raise ValueError("bar_widgets harnesses must be a list or null")
        merged_widgets[widget_id] = merged_patch
    return {
        widget_id: BarWidgetUserConfig.model_validate(values)
        for widget_id, values in merged_widgets.items()
    }


def _patch_startup_rogue(sr_val: Any, valid_harnesses: set[str]) -> dict[str, Any] | None:
    if sr_val is None:
        return None
    if not isinstance(sr_val, dict):
        raise ValueError("startup_rogue must be an object or null")
    harness = sr_val.get("harness")
    if harness not in valid_harnesses:
        raise ValueError(f"invalid startup_rogue harness: {harness!r}")
    effort = sr_val.get("effort")
    if effort is not None and not isinstance(effort, str):
        raise ValueError("startup_rogue effort must be a string or null")
    return {
        "harness": harness,
        "model": str(sr_val.get("model") or ""),
        "effort": effort if (isinstance(effort, str) and effort.strip()) else None,
    }


def _patch_tui(cfg: UserConfig, partial: dict[str, Any], valid_harnesses: set[str]) -> None:
    tui_merged = _tui_base(cfg)
    for key in _TUI_SCALAR_KEYS:
        if key in partial:
            tui_merged[key] = partial[key]
    if "codex_control_backend" in partial:
        tui_merged["codex_control_backend"] = _require_backend(
            partial["codex_control_backend"], CodexControlBackend, "codex_control_backend"
        )
    if "cursor_control_backend" in partial:
        tui_merged["cursor_control_backend"] = _require_backend(
            partial["cursor_control_backend"],
            CursorControlBackend,
            "cursor_control_backend",
        )
    if "claude_control_backend" in partial:
        tui_merged["claude_control_backend"] = _require_backend(
            partial["claude_control_backend"],
            ClaudeControlBackend,
            "claude_control_backend",
        )
    if "bar_widgets" in partial:
        tui_merged["bar_widgets"] = _patch_bar_widgets(
            cfg, partial["bar_widgets"], valid_harnesses
        )
    if "startup_rogue" in partial:
        tui_merged["startup_rogue"] = _patch_startup_rogue(
            partial["startup_rogue"], valid_harnesses
        )
    cfg.tui = TuiUserConfig.model_validate(tui_merged)


def _patch_collaborator(
    cfg: UserConfig, value: Any, valid_harnesses: set[str]
) -> LiveChange:
    if value is None:
        if cfg.collaborator is not None:
            cfg.collaborator.harness = None
        return ClearCollaboratorHarness()
    if value not in valid_harnesses:
        raise ValueError(f"invalid collaborator harness: {value!r}")
    patch = cfg.collaborator or UserHarnessRolePatch()
    patch.harness = value
    cfg.collaborator = patch
    return SetCollaboratorHarness(value)


def _patch_planner(cfg: UserConfig, value: Any, valid_harnesses: set[str]) -> LiveChange:
    if value is None:
        if cfg.planner is not None:
            cfg.planner.harness = None
        return ClearPlannerHarness()
    if value not in valid_harnesses:
        raise ValueError(f"invalid planner harness: {value!r}")
    patch = cfg.planner or UserHarnessRolePatch()
    patch.harness = value
    cfg.planner = patch
    return SetPlannerHarness(value)


def _patch_crow(cfg: UserConfig, value: Any, valid_harnesses: set[str]) -> LiveChange:
    if value is None:
        if cfg.default_crow is not None:
            cfg.default_crow.harness = None
            cfg.default_crow.harnesses = None
        return ClearCrowHarnesses()
    if not isinstance(value, list) or not value:
        raise ValueError("crow_harnesses must be a non-empty list or null")
    for harness in value:
        if harness not in valid_harnesses:
            raise ValueError(f"invalid crow harness: {harness!r}")
    patch = cfg.default_crow or UserHarnessRolePatch()
    if len(value) == 1:
        patch.harness = value[0]
        patch.harnesses = None
        live_harnesses: tuple[UserHarnessKind, ...] | None = None
    else:
        patch.harness = value[0]
        patch.harnesses = list(value)
        live_harnesses = tuple(value)
    cfg.default_crow = patch
    return SetCrowHarnesses(harness=value[0], harnesses=live_harnesses)


def _patch_harness_overrides(
    cfg: UserConfig,
    partial: dict[str, Any],
    valid_harnesses: set[str],
) -> list[LiveChange]:
    """Apply collaborator/planner/crow overrides and return matching live changes.

    Explicit ``null`` clears the persisted override *and* enqueues a clear
    live-change so the running process mirrors a restart.
    """
    live_changes: list[LiveChange] = []
    if "collaborator_harness" in partial:
        live_changes.append(
            _patch_collaborator(cfg, partial["collaborator_harness"], valid_harnesses)
        )
    if "planner_harness" in partial:
        live_changes.append(
            _patch_planner(cfg, partial["planner_harness"], valid_harnesses)
        )
    if "crow_harnesses" in partial:
        live_changes.append(_patch_crow(cfg, partial["crow_harnesses"], valid_harnesses))
    return live_changes


def _patch_llm(cfg: UserConfig, partial: dict[str, Any]) -> None:
    if "llm" not in partial:
        return
    incoming = partial["llm"]
    if not isinstance(incoming, dict):
        raise ValueError("llm must be an object")
    incoming = translate_legacy_llm_provider_fields(incoming)
    existing = cfg.llm.model_dump(mode="json") if cfg.llm is not None else {}
    merged_llm = deep_merge_settings(existing, incoming)
    restore_api_key_sentinels(merged_llm, existing.get("providers") or {})
    cfg.llm = UserLlmConfig.model_validate(merged_llm)


def _patch_execution(cfg: UserConfig, partial: dict[str, Any]) -> None:
    if "execution" not in partial:
        return
    incoming = partial["execution"]
    if not isinstance(incoming, dict):
        raise ValueError("execution must be an object")
    existing_exec = (
        cfg.execution.model_dump(mode="json") if cfg.execution is not None else {}
    )
    cfg.execution = UserExecutionConfig.model_validate(
        deep_merge_settings(existing_exec, incoming)
    )


def _patch_oracle(cfg: UserConfig, partial: dict[str, Any]) -> None:
    if "oracle" not in partial:
        return
    incoming = partial["oracle"]
    if not isinstance(incoming, dict):
        raise ValueError("oracle must be an object")
    existing_oracle = (
        cfg.oracle.model_dump(mode="json") if cfg.oracle is not None else {}
    )
    cfg.oracle = UserOracleConfig.model_validate(
        deep_merge_settings(existing_oracle, incoming)
    )


def apply_settings_patch(current: UserConfig, partial: dict[str, Any]) -> SettingsMutation:
    """Overlay *partial* onto *current* and collect inspectable live changes.

    Absent keys are no-ops. Explicit ``null`` clears harness overrides and
    enqueues the matching clear live-change so the running process stays in
    sync after persist.
    """
    cfg = current.model_copy(deep=True)
    valid_harnesses = set(get_args(UserHarnessKind))
    _patch_tui(cfg, partial, valid_harnesses)
    live_changes = _patch_harness_overrides(cfg, partial, valid_harnesses)
    _patch_llm(cfg, partial)
    _patch_execution(cfg, partial)
    _patch_oracle(cfg, partial)
    return SettingsMutation(config=cfg, live_changes=tuple(live_changes))


__all__ = ["apply_settings_patch"]
