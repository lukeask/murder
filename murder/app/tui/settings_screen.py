"""TUI settings screen — project harness/model config + global user defaults.

Global scope edits `~/.config/murder/config.yaml` (theme + optional
`default_crow` patch). Project scope edits `.murder/roles.yaml`.

Replaces the old `murder config` / `murder --config` CLI flow.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Literal, TypeAlias, cast

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

from murder.config import Config, HarnessKind, HarnessRoleConfig
from murder.llm.harnesses import REGISTRY
from murder.user_config import UserConfig, UserHarnessRolePatch

from murder.app.service.settings_service import ProjectRoleModels, SettingsService

_HARNESS_ROWS: list[tuple[HarnessKind, str, str]] = [
    ("cursor", "Cursor CLI", "agent"),
    ("claude_code", "Claude Code", "claude"),
    ("codex", "Codex CLI", "codex"),
    ("pi", "Pi", "pi"),
    ("antigravity", "Antigravity CLI", "agy"),
    ("native_coding_crow", "Native coding crow", "native_coding_crow"),
]

_API_MODEL_ROWS: list[tuple[str, str]] = [
    ("anthropic/claude-opus-4-7", "Claude Opus 4.7"),
    ("anthropic/claude-sonnet-4-6", "Claude Sonnet 4.6"),
    ("anthropic/claude-haiku-4-5", "Claude Haiku 4.5"),
    ("openai/gpt-5.5", "GPT-5.5"),
    ("openai/gpt-5.4", "GPT-5.4"),
    ("openai/gpt-5.4-mini", "GPT-5.4 Mini"),
    ("qwen/qwen3.6-35b-a3b", "Qwen3.6 35B A3B"),
    ("deepseek/deepseek-v4-flash", "DeepSeek V4 Flash"),
    ("deepseek/deepseek-v4-pro", "DeepSeek V4 Pro"),
]

_API_PROVIDER_ROWS: list[tuple[str, str]] = [
    ("openrouter", "OpenRouter"),
    ("cerebras", "Cerebras"),
    ("groq", "Groq"),
    ("anthropic", "Anthropic (direct)"),
    ("openai", "OpenAI (direct)"),
    ("local", "Local OpenAI-compatible"),
]

_CEREBRAS_MODELS: list[tuple[str, str]] = [
    ("zai-glm-4.7", "ZAI GLM 4.7 (reasoning)"),
]

_GROQ_MODELS: list[tuple[str, str]] = [
    ("openai/gpt-oss-120b", "OpenAI GPT-OSS 120B"),
]

_NOTETAKER_MODELS_BY_PROVIDER: dict[str, list[tuple[str, str]]] = {
    "openrouter": _API_MODEL_ROWS,
    "anthropic": _API_MODEL_ROWS,
    "openai": _API_MODEL_ROWS,
    "local": _API_MODEL_ROWS,
    "cerebras": _CEREBRAS_MODELS,
    "groq": _GROQ_MODELS,
}

ModelState: TypeAlias = Literal["disabled", "enabled", "default"]
_MODEL_STATE_ORDER: tuple[ModelState, ...] = ("disabled", "enabled", "default")


def _read_patch_models(  # noqa: PLR0912 — mirrors the patch's three mutually-exclusive model-spec arms; splitting further fragments one algorithm.
    patch: HarnessRoleConfig | UserHarnessRolePatch | None,
) -> tuple[list[HarnessKind], dict[HarnessKind, list[str]], dict[HarnessKind, str]]:
    """Read the patch's harness/model intent. Pure domain projection.

    Returns (pool, configured_models, configured_defaults). Empty pool
    means "no harnesses enabled." Unified rule for picking a per-harness
    default when `startup_models_by_harness` isn't set: prefer
    `startup_model` if it's in the pool, else fall back to the pool's
    first entry.
    """
    if patch is None:
        return [], {}, {}

    if patch.harnesses:
        pool: list[HarnessKind] = [cast(HarnessKind, h) for h in patch.harnesses]
    elif patch.harness is not None:
        pool = [cast(HarnessKind, patch.harness)]
    else:
        pool = []

    configured_models: dict[HarnessKind, list[str]] = {}
    configured_defaults: dict[HarnessKind, str] = {}

    if patch.startup_models_by_harness:
        for k, ms in patch.startup_models_by_harness.items():
            h = cast(HarnessKind, k)
            configured_models[h] = list(ms)
            if ms:
                configured_defaults[h] = ms[0]
    elif patch.startup_models:
        for h in pool:
            hk = cast(HarnessKind, h)
            configured_models[hk] = list(patch.startup_models)
            if patch.startup_model in patch.startup_models:
                configured_defaults[hk] = cast(str, patch.startup_model)
            else:
                configured_defaults[hk] = patch.startup_models[0]
    elif patch.startup_model is not None:
        for h in pool:
            hk = cast(HarnessKind, h)
            configured_models[hk] = [patch.startup_model]
            configured_defaults[hk] = patch.startup_model

    return pool, configured_models, configured_defaults


def _build_ui_model_state(
    configured_models: dict[HarnessKind, list[str]],
    configured_defaults: dict[HarnessKind, str],
) -> tuple[
    dict[HarnessKind, list[tuple[str, str]]],
    dict[HarnessKind, dict[str, ModelState]],
]:
    """Project domain model selections into the UI's per-harness option
    list (REGISTRY defaults plus anything the patch referenced) and a
    `default`/`enabled`/`disabled` classifier per option."""
    options_by_kind: dict[HarnessKind, list[tuple[str, str]]] = {}
    states_by_kind: dict[HarnessKind, dict[str, ModelState]] = {}
    for kind, _, _ in _HARNESS_ROWS:
        options = list(REGISTRY[kind].available_startup_models)
        seen = {model_id for model_id, _ in options}
        for model_id in configured_models.get(kind, []):
            if model_id not in seen:
                options.append((model_id, model_id))
                seen.add(model_id)
        options_by_kind[kind] = options
        default_model = configured_defaults.get(kind)
        selected = set(configured_models.get(kind, []))
        states_by_kind[kind] = {
            model_id: _classify_model_state(model_id, default_model, selected)
            for model_id, _ in options
        }
    return options_by_kind, states_by_kind


def _classify_model_state(
    model_id: str, default_model: str | None, selected: set[str]
) -> ModelState:
    if model_id == default_model:
        return "default"
    if model_id in selected:
        return "enabled"
    return "disabled"


def _resolve_crow_model_state(
    patch: HarnessRoleConfig | UserHarnessRolePatch | None,
) -> tuple[
    set[HarnessKind],
    dict[HarnessKind, list[tuple[str, str]]],
    dict[HarnessKind, dict[str, ModelState]],
]:
    """Project a harness-role patch into UI-staged settings state.

    Both the project scope (`Config.default_crow`) and the global scope
    (`UserConfig.default_crow`) feed UI state through the same shape.
    Composes patch-reading and UI-building so the two scopes share one
    algorithm — the two used to inline it and had drifted on the
    `startup_models` fallback.
    """
    pool, configured_models, configured_defaults = _read_patch_models(patch)
    options_by_kind, states_by_kind = _build_ui_model_state(configured_models, configured_defaults)
    return set(pool), options_by_kind, states_by_kind


def _sid(s: str) -> str:
    """Sanitize a string for use as a Textual widget ID suffix."""
    return "".join(c if c.isalnum() or c == "_" else "_" for c in s)


def _ordered_enabled_models(model_states: dict[str, ModelState], model_ids: list[str]) -> list[str]:
    defaults = [m for m in model_ids if model_states.get(m) == "default"]
    enabled = [m for m in model_ids if model_states.get(m) == "enabled"]
    return defaults + enabled


def _model_validation_message(
    model_states: dict[str, ModelState], model_ids: list[str]
) -> str | None:
    if not model_ids:
        return None
    selected = [m for m in model_ids if model_states.get(m) in {"enabled", "default"}]
    defaults = [m for m in model_ids if model_states.get(m) == "default"]
    if not selected:
        return "invalid: select at least one model"
    if len(defaults) > 1:
        return "invalid: choose only one default"
    return None


class _SettingItem(Static, can_focus=False):
    """One toggleable settings row — checkbox or radio."""

    DEFAULT_CSS = """
    _SettingItem {
        height: 1;
    }
    _SettingItem.cursor {
        background: $primary;
        color: $background;
    }
    """

    def __init__(
        self,
        kind: str,
        label: str,
        key: str,
        group: str = "",
        indent: int = 0,
        checked: bool = False,
        model_state: ModelState = "disabled",
        item_id: str | None = None,
    ) -> None:
        # markup=False: marker glyphs like "[x]"/"[*]" would otherwise be
        # parsed as Rich markup tags (style "x"/"*") and rendered blank.
        super().__init__(id=item_id, markup=False)
        self._kind = kind
        self._label = label
        self._key = key
        self._group = group
        self._indent = indent
        self._checked = checked
        self._model_state: ModelState = model_state

    def on_mount(self) -> None:
        self._render_label()

    @property
    def key(self) -> str:
        return self._key

    @property
    def group(self) -> str:
        return self._group

    @property
    def checked(self) -> bool:
        return self._checked

    @checked.setter
    def checked(self, value: bool) -> None:
        self._checked = value
        self._render_label()

    @property
    def model_state(self) -> ModelState:
        return self._model_state

    @model_state.setter
    def model_state(self, value: ModelState) -> None:
        self._model_state = value
        self._render_label()

    def _render_label(self) -> None:
        pad = "  " * self._indent
        if self._kind == "cb":
            marker = "[x]" if self._checked else "[ ]"
            suffix = ""
        elif self._kind == "tri":
            marker = {
                "disabled": "[ ]",
                "enabled": "[x]",
                "default": "[*]",
            }[self._model_state]
            suffix = "  default" if self._model_state == "default" else ""
        else:
            marker = "(•)" if self._checked else "( )"
            suffix = ""
        self.update(f"{pad}{marker} {self._label}{suffix}")


class SettingsScreen(ModalScreen[bool]):
    """Settings panel: global (theme + user `default_crow`) and project (roles.yaml)."""

    BINDINGS = [
        Binding("escape", "cancel", "Close"),
        Binding("g", "scope_global", "Global", show=False),
        Binding("p", "scope_project", "Project", show=False),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("h", "item_left", "Left", show=False),
        Binding("l", "item_right", "Right", show=False),
        Binding("a", "toggle_mode", "Basic/Advanced", show=False),
        Binding("enter", "toggle_item", "Toggle", show=False),
        Binding("space", "toggle_item", "Toggle", show=False),
    ]

    CSS = """
    SettingsScreen {
        align: center middle;
    }
    #settings-box {
        width: 84;
        max-width: 92%;
        max-height: 88%;
        border: solid $primary;
        background: $surface;
    }
    #settings-title {
        background: $primary;
        color: $background;
        text-align: center;
        height: 1;
        padding: 0 2;
        text-style: bold;
    }
    #scope-bar {
        height: 1;
        padding: 0 2;
        background: $panel;
        color: $text-muted;
    }
    #scroll {
        padding: 0 2;
        height: 1fr;
    }
    #scroll Vertical {
        /* Section containers size to content; without this they inherit
           Vertical's default height:1fr, get squeezed to ~1 row inside the
           scroll, and clip their children (the per-harness model pickers). */
        height: auto;
    }
    .section-header {
        height: 1;
        color: $primary;
        margin-top: 1;
        text-style: bold;
    }
    .model-status {
        height: 1;
        color: red;
        margin-left: 2;
    }
    #help-bar {
        height: 1;
        padding: 0 1;
        background: $panel;
        color: $text-muted;
        text-align: center;
    }
    """

    def __init__(
        self,
        config: Config,
        repo: Path,
        user_config: UserConfig,
        available_themes: list[str],
        *,
        settings_service: SettingsService | None = None,
    ) -> None:
        super().__init__()
        self._config = config
        self._repo = repo
        self._settings = settings_service or SettingsService(repo)
        self._user_config = user_config
        self._available_themes = available_themes

        # Global state (theme + default crow UI; written on edit via autosave)
        self._theme_sel: str = user_config.tui.theme or (
            available_themes[0] if available_themes else ""
        )

        # Project state — resolved via the shared patch projector.
        (
            self._harnesses,
            self._model_options,
            self._model_states,
        ) = _resolve_crow_model_state(config.default_crow)
        self._model_discovery_attempted: set[HarnessKind] = set()
        self._crow_handler_model: str = config.crow_handler.model
        self._crow_handler_auto_free: bool = config.crow_handler.auto_free
        self._notetaker_provider: str = config.notetaker.provider
        self._notetaker_model: str = config.notetaker.model
        self._notetaker_auto_free: bool = config.notetaker.auto_free
        self._collaborator_harness: HarnessKind = config.collaborator.harness
        self._planner_harness: HarnessKind = config.planner.harness

        # Global default_crow (staged from `user_config.default_crow` only — never merged
        # project/bundled effective config).
        self._initial_default_crow: UserHarnessRolePatch | None = (
            user_config.default_crow.model_copy(deep=True)
            if user_config.default_crow is not None
            else None
        )
        self._global_model_discovery_attempted: set[HarnessKind] = set()
        self._populate_global_crow_from_patch(user_config.default_crow)

        self._scope = "project"
        self._mode: Literal["basic", "advanced"] = "basic"
        self._cursor_idx = 0
        self._focusable: list[_SettingItem] = []
        self._written_since_open = False

    def _populate_global_crow_from_patch(self, patch: UserHarnessRolePatch | None) -> None:
        """Initialize global crow UI from the user file only (no project merge)."""
        (
            self._global_harnesses,
            self._global_model_options,
            self._global_model_states,
        ) = _resolve_crow_model_state(patch)

    # ── compose ────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        with Container(id="settings-box"):
            yield Static("⚙  Settings", id="settings-title")
            yield Static("", id="scope-bar")
            with VerticalScroll(id="scroll"):
                # Global section: theme
                with Vertical(id="section-global"):
                    yield Static(
                        "── GLOBAL  (~/.config/murder/) ──",
                        classes="section-header",
                    )
                    yield Static(
                        "── THEME ──",
                        classes="section-header",
                    )
                    for name in self._available_themes:
                        yield _SettingItem(
                            "radio",
                            name,
                            key=f"theme:{name}",
                            group="theme",
                            checked=(name == self._theme_sel),
                            item_id=f"item-theme-{_sid(name)}",
                        )
                    yield Static(
                        "── ENABLED CROW HARNESSES  (your default crow) ──",
                        classes="section-header",
                    )
                    for kind, label, exe in _HARNESS_ROWS:
                        avail = "✓" if shutil.which(exe) else "✗"
                        yield _SettingItem(
                            "cb",
                            f"{label}  [{avail}]",
                            key=f"global_harness:{kind}",
                            checked=(kind in self._global_harnesses),
                            item_id=f"item-global-harness-{kind}",
                        )
                    for kind, label, _ in _HARNESS_ROWS:
                        options = self._global_model_options[kind]
                        section = Vertical(id=f"global-section-models-{_sid(kind)}")
                        section.display = kind in self._global_harnesses
                        with section:
                            yield Static(
                                f"── {label.upper()} MODELS  (global) ──",
                                id=f"global-header-models-{_sid(kind)}",
                                classes="section-header",
                            )
                            status = Static(
                                "",
                                id=f"global-status-models-{_sid(kind)}",
                                classes="model-status",
                            )
                            status.display = False
                            yield status
                            for model_id, model_label in options:
                                yield self._global_model_item(kind, model_id, model_label)

                # Project section: harnesses, models, API roles
                with Vertical(id="section-project"):
                    yield Static(
                        "── ENABLED CROW HARNESSES  (project: .murder/) ──",
                        classes="section-header",
                    )
                    for kind, label, exe in _HARNESS_ROWS:
                        avail = "✓" if shutil.which(exe) else "✗"
                        yield _SettingItem(
                            "cb",
                            f"{label}  [{avail}]",
                            key=f"harness:{kind}",
                            checked=(kind in self._harnesses),
                            item_id=f"item-harness-{kind}",
                        )
                    for kind, label, _ in _HARNESS_ROWS:
                        options = self._model_options[kind]
                        section = Vertical(id=f"section-models-{_sid(kind)}")
                        section.display = kind in self._harnesses
                        with section:
                            yield Static(
                                f"── {label.upper()} MODELS  (project) ──",
                                id=f"header-models-{_sid(kind)}",
                                classes="section-header",
                            )
                            status = Static(
                                "",
                                id=f"status-models-{_sid(kind)}",
                                classes="model-status",
                            )
                            status.display = False
                            yield status
                            for model_id, model_label in options:
                                yield self._model_item(kind, model_id, model_label)
                    with Vertical(id="section-internal-model"):
                        yield Static(
                            "── INTERNAL MODEL  (project) ──",
                            classes="section-header",
                        )
                        yield _SettingItem(
                            "radio",
                            "Auto Free",
                            key="auto_free_mode:auto",
                            group="auto_free_mode",
                            checked=self._internal_auto_free_enabled(),
                            item_id="item-auto-free-mode-auto",
                        )
                        yield _SettingItem(
                            "radio",
                            "Manual",
                            key="auto_free_mode:manual",
                            group="auto_free_mode",
                            checked=not self._internal_auto_free_enabled(),
                            item_id="item-auto-free-mode-manual",
                        )
                        with Vertical(id="section-internal-manual"):
                            yield Static(
                                "── PROVIDER  (project) ──",
                                classes="section-header",
                            )
                            for provider_id, provider_label in _API_PROVIDER_ROWS:
                                yield _SettingItem(
                                    "radio",
                                    provider_label,
                                    key=f"internal_provider:{provider_id}",
                                    group="internal_provider",
                                    checked=(provider_id == self._notetaker_provider),
                                    item_id=f"item-internal-provider-{_sid(provider_id)}",
                                )
                            yield Static(
                                "── MODEL  (project) ──",
                                classes="section-header",
                            )
                            with Vertical(id="section-internal-model-rows"):
                                for model_id, model_label in self._internal_model_rows():
                                    yield _SettingItem(
                                        "radio",
                                        model_label,
                                        key=f"internal_model:{model_id}",
                                        group="internal_model",
                                        checked=(model_id == self._notetaker_model),
                                        item_id=f"item-internal-model-{_sid(model_id)}",
                                    )
                    yield Static(
                        "── COLLABORATOR HARNESS  (project) ──",
                        classes="section-header",
                    )
                    for kind, label, exe in _HARNESS_ROWS:
                        avail = "✓" if shutil.which(exe) else "✗"
                        yield _SettingItem(
                            "radio",
                            f"{label}  [{avail}]",
                            key=f"collab_harness:{kind}",
                            group="collab_harness",
                            checked=(kind == self._collaborator_harness),
                            item_id=f"item-collab_harness-{_sid(kind)}",
                        )
                    yield Static(
                        "── PLANNING AGENT HARNESS  (project) ──",
                        classes="section-header",
                    )
                    for kind, label, exe in _HARNESS_ROWS:
                        avail = "✓" if shutil.which(exe) else "✗"
                        yield _SettingItem(
                            "radio",
                            f"{label}  [{avail}]",
                            key=f"planner_harness:{kind}",
                            group="planner_harness",
                            checked=(kind == self._planner_harness),
                            item_id=f"item-planner_harness-{_sid(kind)}",
                        )
                    with Vertical(id="section-api-roles"):
                        yield Static(
                            "── NOTETAKER PROVIDER  (project) ──",
                            classes="section-header",
                        )
                        for provider_id, provider_label in _API_PROVIDER_ROWS:
                            yield _SettingItem(
                                "radio",
                                provider_label,
                                key=f"notetaker_provider:{provider_id}",
                                group="notetaker_provider",
                                checked=(provider_id == self._notetaker_provider),
                                item_id=f"item-notetaker-provider-{_sid(provider_id)}",
                            )
                        yield Static(
                            "── NOTETAKER MODEL  (project) ──",
                            id="notetaker-model-header",
                            classes="section-header",
                        )
                        for model_id, model_label in _NOTETAKER_MODELS_BY_PROVIDER.get(
                            self._notetaker_provider, _API_MODEL_ROWS
                        ):
                            yield _SettingItem(
                                "radio",
                                model_label,
                                key=f"notetaker:{model_id}",
                                group="notetaker",
                                checked=(model_id == self._notetaker_model),
                                item_id=f"item-notetaker-{_sid(model_id)}",
                            )
                        yield Static(
                            "── CROW HANDLER MODEL  (project) ──",
                            classes="section-header",
                        )
                        for model_id, model_label in _API_MODEL_ROWS:
                            yield _SettingItem(
                                "radio",
                                model_label,
                                key=f"crow_handler:{model_id}",
                                group="crow_handler",
                                checked=(model_id == self._crow_handler_model),
                                item_id=f"item-crow_handler-{_sid(model_id)}",
                            )
            yield Static(
                "j/k move  h/l ←→  ↑↓ scroll  enter/spc toggle  "
                "a basic/adv  g global  p project  esc close",
                id="help-bar",
            )

    def on_mount(self) -> None:
        self._apply_scope()
        self._apply_mode()
        self.query_one("#scroll").focus()
        self._refresh_model_validation()
        self._refresh_global_model_validation()
        self.run_worker(
            self._refresh_enabled_harness_model_options(),
            exclusive=True,
            group="settings_models",
        )
        self.run_worker(
            self._refresh_global_enabled_harness_model_options(),
            exclusive=True,
            group="settings_global_models",
        )

    # ── scope ──────────────────────────────────────────────────────────────

    def _apply_scope(self) -> None:
        self.query_one("#section-global").display = self._scope == "global"
        self.query_one("#section-project").display = self._scope == "project"
        active = self._scope
        self.query_one("#scope-bar", Static).update(
            f"{'→ ' if active == 'global' else '  '}[g] global    "
            f"{'→ ' if active == 'project' else '  '}[p] project"
        )
        old_key = self._focusable[self._cursor_idx].key if self._focusable else None
        self._rebuild_focusable()
        # Restore cursor to same key if visible, else reset to 0
        if old_key:
            for i, item in enumerate(self._focusable):
                if item.key == old_key:
                    self._cursor_idx = i
                    break
            else:
                self._cursor_idx = 0
        else:
            self._cursor_idx = 0
        self._refresh_cursor()

    def action_scope_global(self) -> None:
        self._scope = "global"
        self._apply_scope()

    def action_scope_project(self) -> None:
        self._scope = "project"
        self._apply_scope()

    # ── mode ───────────────────────────────────────────────────────────────

    def action_toggle_mode(self) -> None:
        self._mode = "advanced" if self._mode == "basic" else "basic"
        self._apply_mode()

    def _apply_mode(self) -> None:
        old_key = self._focusable[self._cursor_idx].key if self._focusable else None
        basic = self._mode == "basic"
        self.query_one("#section-internal-model").display = basic
        self.query_one("#section-internal-manual").display = (
            basic and not self._internal_auto_free_enabled()
        )
        self.query_one("#section-api-roles").display = not basic
        for kind in [k for k, _, _ in _HARNESS_ROWS]:
            self._set_model_section_display(kind, kind in self._harnesses)
        self._rebuild_focusable()
        if old_key:
            self._cursor_idx = next(
                (i for i, item in enumerate(self._focusable) if item.key == old_key),
                min(self._cursor_idx, max(0, len(self._focusable) - 1)),
            )
        else:
            self._cursor_idx = min(self._cursor_idx, max(0, len(self._focusable) - 1))
        self._refresh_cursor()

    def _internal_auto_free_enabled(self) -> bool:
        return self._crow_handler_auto_free and self._notetaker_auto_free

    def _internal_model_rows(self) -> list[tuple[str, str]]:
        return _NOTETAKER_MODELS_BY_PROVIDER.get(self._notetaker_provider, _API_MODEL_ROWS)

    # ── focusable list ─────────────────────────────────────────────────────

    def _rebuild_focusable(self) -> None:
        section_id = "section-global" if self._scope == "global" else "section-project"
        section = self.query_one(f"#{section_id}")
        # `w.display` is the widget's *own* flag; a model row inside a hidden
        # `section-models-<kind>` container still reports display=True, so we
        # must also confirm every ancestor up the tree is shown.
        self._focusable = [
            w
            for w in section.query(_SettingItem)
            if all(getattr(node, "display", True) for node in w.ancestors_with_self)
        ]

    def _refresh_cursor(self) -> None:
        for i, item in enumerate(self._focusable):
            item.set_class(i == self._cursor_idx, "cursor")
        if self._focusable and 0 <= self._cursor_idx < len(self._focusable):
            self._focusable[self._cursor_idx].scroll_visible()

    # ── navigation ─────────────────────────────────────────────────────────

    def action_cursor_down(self) -> None:
        if not self._focusable:
            return
        self._cursor_idx = (self._cursor_idx + 1) % len(self._focusable)
        self._refresh_cursor()

    def action_cursor_up(self) -> None:
        if not self._focusable:
            return
        self._cursor_idx = (self._cursor_idx - 1) % len(self._focusable)
        self._refresh_cursor()

    def _focused_item(self) -> _SettingItem | None:
        if not self._focusable or self._cursor_idx >= len(self._focusable):
            return None
        return self._focusable[self._cursor_idx]

    def _radio_neighbor(self, item: _SettingItem, direction: int) -> _SettingItem | None:
        mates = [f for f in self._focusable if f.group == item.group]
        if not mates:
            return None
        try:
            idx = mates.index(item)
        except ValueError:
            return mates[0]
        return mates[(idx + direction) % len(mates)]

    def _cycle_tri_item(self, item: _SettingItem, direction: int) -> None:
        kind, *rest_parts = item.key.split(":", 1)
        payload = rest_parts[0] if rest_parts else ""
        if kind == "model":
            harness_s, model_id = payload.split(":", 1)
            harness_kind = cast(HarnessKind, harness_s)
            states = self._model_states.setdefault(harness_kind, {})
            state = states.get(model_id, "disabled")
            next_index = (_MODEL_STATE_ORDER.index(state) + direction) % len(_MODEL_STATE_ORDER)
            next_state = _MODEL_STATE_ORDER[next_index]
            states[model_id] = next_state
            item.model_state = next_state
            self._refresh_model_validation()
            self._try_autosave_project()
        elif kind == "global_model":
            harness_s, model_id = payload.split(":", 1)
            harness_kind = cast(HarnessKind, harness_s)
            states = self._global_model_states.setdefault(harness_kind, {})
            state = states.get(model_id, "disabled")
            next_index = (_MODEL_STATE_ORDER.index(state) + direction) % len(_MODEL_STATE_ORDER)
            next_state = _MODEL_STATE_ORDER[next_index]
            states[model_id] = next_state
            item.model_state = next_state
            self._refresh_global_model_validation()
            self._try_autosave_global()

    def _autosave_for_kind(self, kind: str) -> None:
        if kind in {
            "harness",
            "model",
            "crow_handler",
            "notetaker",
            "notetaker_provider",
            "auto_free_mode",
            "internal_provider",
            "internal_model",
            "collab_harness",
            "planner_harness",
        }:
            self._try_autosave_project()
        elif kind in {"theme", "global_harness", "global_model"}:
            self._try_autosave_global()

    def _apply_item(self, item: _SettingItem) -> str | None:
        kind, *rest_parts = item.key.split(":", 1)
        payload = rest_parts[0] if rest_parts else ""

        if kind == "harness":
            self._toggle_harness(item, cast(HarnessKind, payload))
        elif kind == "model":
            harness_s, model_id = payload.split(":", 1)
            self._toggle_model(item, cast(HarnessKind, harness_s), model_id)
        elif kind == "theme":
            self._theme_sel = payload
            self._refresh_radio_group("theme")
        elif kind == "crow_handler":
            self._crow_handler_model = payload
            self._refresh_radio_group("crow_handler")
        elif kind == "notetaker_provider":
            self._notetaker_provider = payload
            self._refresh_radio_group("notetaker_provider")
            self.run_worker(
                self._rebuild_notetaker_model_rows(),
                exclusive=True,
                group="notetaker_models",
            )
            self.run_worker(
                self._rebuild_internal_model_rows(reset_selection=False),
                exclusive=True,
                group="internal_models",
            )
        elif kind == "notetaker":
            self._notetaker_model = payload
            self._refresh_radio_group("notetaker")
        elif kind == "auto_free_mode":
            enabled = payload == "auto"
            self._crow_handler_auto_free = enabled
            self._notetaker_auto_free = enabled
            self._refresh_radio_group("auto_free_mode")
            self._apply_mode()
        elif kind == "internal_provider":
            self._notetaker_provider = payload
            self._select_first_internal_model_for_provider()
            self._refresh_radio_group("internal_provider")
            self.run_worker(
                self._rebuild_internal_model_rows(reset_selection=False),
                exclusive=True,
                group="internal_models",
            )
        elif kind == "internal_model":
            self._notetaker_model = payload
            self._crow_handler_model = payload
            self._refresh_radio_group("internal_model")
        elif kind == "collab_harness":
            self._collaborator_harness = cast(HarnessKind, payload)
            self._refresh_radio_group("collab_harness")
        elif kind == "planner_harness":
            self._planner_harness = cast(HarnessKind, payload)
            self._refresh_radio_group("planner_harness")
        elif kind == "global_harness":
            self._toggle_global_harness(item, cast(HarnessKind, payload))
        elif kind == "global_model":
            harness_s, model_id = payload.split(":", 1)
            self._toggle_global_model(item, cast(HarnessKind, harness_s), model_id)
        else:
            return None

        self._autosave_for_kind(kind)
        return kind

    # ── toggle ─────────────────────────────────────────────────────────────

    def action_toggle_item(self) -> None:
        item = self._focused_item()
        if item is None:
            return
        self._apply_item(item)

    def action_item_right(self) -> None:
        item = self._focused_item()
        if item is None:
            return
        if item._kind == "tri":
            self._cycle_tri_item(item, 1)
        elif item._kind == "radio":
            neighbor = self._radio_neighbor(item, 1)
            if neighbor is not None:
                self._apply_item(neighbor)
        elif item._kind == "cb" and not item.checked:
            self._apply_item(item)

    def action_item_left(self) -> None:
        item = self._focused_item()
        if item is None:
            return
        if item._kind == "tri":
            self._cycle_tri_item(item, -1)
        elif item._kind == "radio":
            neighbor = self._radio_neighbor(item, -1)
            if neighbor is not None:
                self._apply_item(neighbor)
        elif item._kind == "cb" and item.checked:
            self._apply_item(item)

    def _toggle_harness(self, item: _SettingItem, kind: HarnessKind) -> None:
        if kind in self._harnesses:
            if len(self._harnesses) <= 1:
                self.notify("At least one harness must remain.", timeout=2)
                return
            self._harnesses.discard(kind)
            self._set_model_section_display(kind, False)
        else:
            self._harnesses.add(kind)
            if not _ordered_enabled_models(
                self._model_states.get(kind, {}),
                self._model_ids(kind),
            ):
                first = next(iter(self._model_ids(kind)), None)
                if first:
                    self._model_states[kind][first] = "default"
            self._set_model_section_display(kind, True)
            self._start_model_discovery(kind)
        item.checked = kind in self._harnesses
        self._refresh_model_validation()
        saved_key = item.key
        self._rebuild_focusable()
        self._cursor_idx = next((i for i, f in enumerate(self._focusable) if f.key == saved_key), 0)
        self._refresh_cursor()

    def _toggle_model(self, item: _SettingItem, kind: HarnessKind, model_id: str) -> None:
        state = self._model_states.setdefault(kind, {}).get(model_id, "disabled")
        next_index = (_MODEL_STATE_ORDER.index(state) + 1) % len(_MODEL_STATE_ORDER)
        next_state = _MODEL_STATE_ORDER[next_index]
        self._model_states[kind][model_id] = next_state
        item.model_state = next_state
        self._refresh_model_validation()

    def _toggle_global_harness(self, item: _SettingItem, kind: HarnessKind) -> None:
        if kind in self._global_harnesses:
            self._global_harnesses.discard(kind)
            self._set_global_model_section_display(kind, False)
        else:
            self._global_harnesses.add(kind)
            if not _ordered_enabled_models(
                self._global_model_states.get(kind, {}),
                self._global_model_ids(kind),
            ):
                first = next(iter(self._global_model_ids(kind)), None)
                if first:
                    self._global_model_states[kind][first] = "default"
            self._set_global_model_section_display(kind, True)
            self._start_global_model_discovery(kind)
        item.checked = kind in self._global_harnesses
        self._refresh_global_model_validation()
        saved_key = item.key
        self._rebuild_focusable()
        self._cursor_idx = next((i for i, f in enumerate(self._focusable) if f.key == saved_key), 0)
        self._refresh_cursor()

    def _toggle_global_model(self, item: _SettingItem, kind: HarnessKind, model_id: str) -> None:
        state = self._global_model_states.setdefault(kind, {}).get(model_id, "disabled")
        next_index = (_MODEL_STATE_ORDER.index(state) + 1) % len(_MODEL_STATE_ORDER)
        next_state = _MODEL_STATE_ORDER[next_index]
        self._global_model_states[kind][model_id] = next_state
        item.model_state = next_state
        self._refresh_global_model_validation()

    def _refresh_radio_group(self, group: str) -> None:
        group_value = {
            "theme": self._theme_sel,
            "crow_handler": self._crow_handler_model,
            "notetaker": self._notetaker_model,
            "notetaker_provider": self._notetaker_provider,
            "auto_free_mode": "auto" if self._internal_auto_free_enabled() else "manual",
            "internal_provider": self._notetaker_provider,
            "internal_model": self._notetaker_model,
            "collab_harness": self._collaborator_harness,
            "planner_harness": self._planner_harness,
        }[group]
        for item in self.query(_SettingItem):
            if item.group == group:
                item.checked = item.key == f"{group}:{group_value}"

    def _model_widget(self, kind: HarnessKind, model_id: str) -> _SettingItem:
        return self.query_one(f"#item-model-{_sid(kind)}-{_sid(model_id)}", _SettingItem)

    def _model_item(self, kind: HarnessKind, model_id: str, model_label: str) -> _SettingItem:
        return _SettingItem(
            "tri",
            model_label,
            key=f"model:{kind}:{model_id}",
            indent=1,
            model_state=self._model_states[kind].get(model_id, "disabled"),
            item_id=f"item-model-{_sid(kind)}-{_sid(model_id)}",
        )

    def _global_model_widget(self, kind: HarnessKind, model_id: str) -> _SettingItem:
        return self.query_one(f"#item-global-model-{_sid(kind)}-{_sid(model_id)}", _SettingItem)

    def _global_model_item(
        self, kind: HarnessKind, model_id: str, model_label: str
    ) -> _SettingItem:
        return _SettingItem(
            "tri",
            model_label,
            key=f"global_model:{kind}:{model_id}",
            indent=1,
            model_state=self._global_model_states[kind].get(model_id, "disabled"),
            item_id=f"item-global-model-{_sid(kind)}-{_sid(model_id)}",
        )

    def _global_model_ids(self, kind: HarnessKind) -> list[str]:
        return [model_id for model_id, _ in self._global_model_options.get(kind, [])]

    def _set_global_model_section_display(self, kind: HarnessKind, visible: bool) -> None:
        self.query_one(f"#global-section-models-{_sid(kind)}").display = visible
        for model_id in self._global_model_ids(kind):
            widget = self._global_model_widget(kind, model_id)
            widget.model_state = self._global_model_states.get(kind, {}).get(model_id, "disabled")

    def _refresh_global_model_validation(self) -> None:
        for kind, _, _ in _HARNESS_ROWS:
            if not self._global_model_options.get(kind):
                continue
            status = self.query_one(f"#global-status-models-{_sid(kind)}", Static)
            message = None
            if kind in self._global_harnesses:
                message = _model_validation_message(
                    self._global_model_states.get(kind, {}),
                    self._global_model_ids(kind),
                )
            if message:
                status.update(message)
                status.display = True
            else:
                status.update("")
                status.display = False

    def _start_global_model_discovery(self, kind: HarnessKind) -> None:
        if kind in self._global_model_discovery_attempted:
            return
        self.run_worker(
            self._refresh_global_harness_model_options(kind),
            exclusive=False,
            group=f"settings_global_models_{kind}",
        )

    async def _refresh_global_enabled_harness_model_options(self) -> None:
        for kind in [k for k, _, _ in _HARNESS_ROWS if k in self._global_harnesses]:
            await self._refresh_global_harness_model_options(kind)

    async def _refresh_global_harness_model_options(self, kind: HarnessKind) -> None:
        if kind in self._global_model_discovery_attempted:
            return
        self._global_model_discovery_attempted.add(kind)
        result = await self._settings.discover_models(kind)
        if not result.ok or not result.models:
            return
        await self._replace_global_model_options(kind, list(result.models))

    async def _replace_global_model_options(
        self, kind: HarnessKind, options: list[tuple[str, str]]
    ) -> None:
        cleaned = self._dedupe_model_options(options)
        if not cleaned:
            return
        old_key = self._focusable[self._cursor_idx].key if self._focusable else None
        old_states = self._global_model_states.get(kind, {})
        self._global_model_options[kind] = cleaned
        self._global_model_states[kind] = {
            model_id: old_states.get(model_id, "disabled") for model_id, _ in cleaned
        }

        section = self.query_one(f"#global-section-models-{_sid(kind)}")
        for item in list(section.query(_SettingItem)):
            await item.remove()
        await section.mount(
            *(self._global_model_item(kind, model_id, label) for model_id, label in cleaned)
        )
        section.display = kind in self._global_harnesses
        self._refresh_global_model_validation()
        self._rebuild_focusable()
        if old_key:
            self._cursor_idx = next(
                (i for i, item in enumerate(self._focusable) if item.key == old_key),
                min(self._cursor_idx, max(0, len(self._focusable) - 1)),
            )
        self._refresh_cursor()
        self._try_autosave_global()

    def _model_ids(self, kind: HarnessKind) -> list[str]:
        return [model_id for model_id, _ in self._model_options.get(kind, [])]

    def _set_model_section_display(self, kind: HarnessKind, visible: bool) -> None:
        self.query_one(f"#section-models-{_sid(kind)}").display = (
            visible and self._mode == "advanced"
        )
        for model_id in self._model_ids(kind):
            widget = self._model_widget(kind, model_id)
            widget.model_state = self._model_states.get(kind, {}).get(model_id, "disabled")

    def _refresh_model_validation(self) -> None:
        for kind, _, _ in _HARNESS_ROWS:
            if not self._model_options.get(kind):
                continue
            status = self.query_one(f"#status-models-{_sid(kind)}", Static)
            message = None
            if kind in self._harnesses:
                message = _model_validation_message(
                    self._model_states.get(kind, {}),
                    self._model_ids(kind),
                )
            if message:
                status.update(message)
                status.display = True
            else:
                status.update("")
                status.display = False

    def _project_validation_messages(self) -> list[str]:
        messages: list[str] = []
        for kind, label, _ in _HARNESS_ROWS:
            if kind not in self._harnesses:
                continue
            message = _model_validation_message(
                self._model_states.get(kind, {}),
                self._model_ids(kind),
            )
            if message:
                messages.append(f"{label}: {message}")
        return messages

    def _start_model_discovery(self, kind: HarnessKind) -> None:
        if kind in self._model_discovery_attempted:
            return
        self.run_worker(
            self._refresh_harness_model_options(kind),
            exclusive=False,
            group=f"settings_models_{kind}",
        )

    async def _refresh_enabled_harness_model_options(self) -> None:
        for kind in [k for k, _, _ in _HARNESS_ROWS if k in self._harnesses]:
            await self._refresh_harness_model_options(kind)

    async def _refresh_harness_model_options(self, kind: HarnessKind) -> None:
        if kind in self._model_discovery_attempted:
            return
        self._model_discovery_attempted.add(kind)
        result = await self._settings.discover_models(kind)
        if not result.ok or not result.models:
            return
        await self._replace_model_options(kind, list(result.models))

    async def _replace_model_options(
        self, kind: HarnessKind, options: list[tuple[str, str]]
    ) -> None:
        cleaned = self._dedupe_model_options(options)
        if not cleaned:
            return
        old_key = self._focusable[self._cursor_idx].key if self._focusable else None
        old_states = self._model_states.get(kind, {})
        self._model_options[kind] = cleaned
        self._model_states[kind] = {
            model_id: old_states.get(model_id, "disabled") for model_id, _ in cleaned
        }

        section = self.query_one(f"#section-models-{_sid(kind)}")
        for item in list(section.query(_SettingItem)):
            await item.remove()
        await section.mount(
            *(self._model_item(kind, model_id, label) for model_id, label in cleaned)
        )
        section.display = kind in self._harnesses and self._mode == "advanced"
        self._refresh_model_validation()
        self._rebuild_focusable()
        if old_key:
            self._cursor_idx = next(
                (i for i, item in enumerate(self._focusable) if item.key == old_key),
                min(self._cursor_idx, max(0, len(self._focusable) - 1)),
            )
        self._refresh_cursor()
        self._try_autosave_project()

    async def _rebuild_notetaker_model_rows(self) -> None:
        """Swap out notetaker model radio rows to match the newly-selected provider."""
        new_rows = _NOTETAKER_MODELS_BY_PROVIDER.get(self._notetaker_provider, _API_MODEL_ROWS)
        # Reset the model selection to the first option for the new provider.
        if new_rows:
            self._notetaker_model = new_rows[0][0]

        section = self.query_one("#section-project")
        # Remove all existing notetaker model items.
        for item in list(section.query(_SettingItem)):
            if item.group == "notetaker":
                await item.remove()

        # Find the NOTETAKER MODEL header to mount after it.
        header_static = self.query_one("#notetaker-model-header", Static)
        new_items = [
            _SettingItem(
                "radio",
                model_label,
                key=f"notetaker:{model_id}",
                group="notetaker",
                checked=(model_id == self._notetaker_model),
                item_id=f"item-notetaker-{_sid(model_id)}",
            )
            for model_id, model_label in new_rows
        ]
        await header_static.mount(*new_items, after=header_static)
        self._rebuild_focusable()
        self._refresh_cursor()
        self._try_autosave_project()

    def _select_first_internal_model_for_provider(self) -> None:
        rows = self._internal_model_rows()
        model_id = rows[0][0] if rows else ""
        self._notetaker_model = model_id
        self._crow_handler_model = model_id

    async def _rebuild_internal_model_rows(self, *, reset_selection: bool) -> None:
        """Swap out basic-mode internal model rows for the selected provider."""
        if reset_selection:
            self._select_first_internal_model_for_provider()

        section = self.query_one("#section-internal-model-rows")
        for item in list(section.query(_SettingItem)):
            await item.remove()
        await section.mount(
            *(
                _SettingItem(
                    "radio",
                    model_label,
                    key=f"internal_model:{model_id}",
                    group="internal_model",
                    checked=(model_id == self._notetaker_model),
                    item_id=f"item-internal-model-{_sid(model_id)}",
                )
                for model_id, model_label in self._internal_model_rows()
            )
        )
        self._refresh_radio_group("internal_provider")
        self._refresh_radio_group("internal_model")
        self._rebuild_focusable()
        self._cursor_idx = min(self._cursor_idx, max(0, len(self._focusable) - 1))
        self._refresh_cursor()

    def _dedupe_model_options(self, options: list[tuple[str, str]]) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        seen: set[str] = set()
        for raw_model_id, raw_label in options:
            model_id = str(raw_model_id).strip()
            label = str(raw_label).strip() or model_id
            if not model_id or model_id in seen:
                continue
            seen.add(model_id)
            out.append((model_id, label))
        return out

    # ── persist (autosave on edit) ─────────────────────────────────────────

    def _try_autosave_project(self) -> None:
        if self._save_project():
            self._written_since_open = True

    def _try_autosave_global(self) -> None:
        self._save_global()
        self._written_since_open = True

    def action_cancel(self) -> None:
        self.dismiss(self._written_since_open)

    def _global_default_crow_save_update(self) -> None:
        """Sync ``default_crow`` on ``self._user_config`` from global UI when valid."""
        harness_list = [k for k, _, _ in _HARNESS_ROWS if k in self._global_harnesses]
        if not harness_list:
            self._user_config.default_crow = None
            return
        for kind in harness_list:
            if (
                _model_validation_message(
                    self._global_model_states.get(kind, {}),
                    self._global_model_ids(kind),
                )
                is not None
            ):
                return
        crow: dict[str, object] = {
            "harness": harness_list[0],
            "harnesses": harness_list if len(harness_list) > 1 else None,
        }
        if len(harness_list) == 1:
            h = harness_list[0]
            model_list = _ordered_enabled_models(
                self._global_model_states.get(h, {}),
                self._global_model_ids(h),
            )
            crow["startup_model"] = model_list[0] if model_list else None
            crow["startup_models"] = model_list if len(model_list) > 1 else None
            crow["startup_models_by_harness"] = None
        else:
            by_harness: dict[str, list[str]] = {}
            for h in harness_list:
                ml = _ordered_enabled_models(
                    self._global_model_states.get(h, {}),
                    self._global_model_ids(h),
                )
                if ml:
                    by_harness[str(h)] = ml
            crow["startup_model"] = None
            crow["startup_models"] = None
            crow["startup_models_by_harness"] = by_harness or None
        self._user_config.default_crow = UserHarnessRolePatch.model_validate(crow)

    def _save_global(self) -> None:
        self._user_config.tui.theme = self._theme_sel or None
        self._global_default_crow_save_update()
        result = self._settings.save_global(self._user_config)
        if not result.ok and result.error:
            self.notify(result.error, severity="error", timeout=5)

    def _save_project(self) -> bool:
        validation_messages = self._project_validation_messages()
        if validation_messages:
            self._refresh_model_validation()
            self.notify(
                "Invalid model settings; fix red messages first.",
                severity="error",
                timeout=5,
            )
            return False

        crow = self._staged_crow_config({})
        try:
            HarnessRoleConfig.model_validate(crow)
        except Exception as exc:
            self.notify(f"Invalid config: {exc}", severity="error", timeout=6)
            return False

        result = self._settings.save_project(
            default_crow=crow,
            role_models=ProjectRoleModels(
                crow_handler_model=self._crow_handler_model,
                collaborator_harness=self._collaborator_harness,
                notetaker_model=self._notetaker_model,
                crow_handler_auto_free=self._crow_handler_auto_free,
                notetaker_provider=self._notetaker_provider,
                notetaker_auto_free=self._notetaker_auto_free,
                planner_harness=self._planner_harness,
            ),
        )
        if not result.ok:
            self.notify(
                result.error or "Failed to save project settings.",
                severity="error",
                timeout=5,
            )
            return False
        return True

    def _staged_harness_list(self) -> list[HarnessKind]:
        # Build ordered harness list (preserve _HARNESS_ROWS order)
        harness_list: list[HarnessKind] = [k for k, _, _ in _HARNESS_ROWS if k in self._harnesses]
        if not harness_list:
            harness_list = ["cursor"]
        return harness_list

    def _staged_crow_config(self, raw: dict) -> dict:
        harness_list = self._staged_harness_list()
        crow: dict = raw.get("default_crow") or {}
        crow["harness"] = harness_list[0]
        crow["harnesses"] = harness_list if len(harness_list) > 1 else None

        if len(harness_list) == 1:
            h = harness_list[0]
            model_list = _ordered_enabled_models(
                self._model_states.get(h, {}),
                self._model_ids(h),
            )
            crow["startup_model"] = model_list[0] if model_list else None
            crow["startup_models"] = model_list if len(model_list) > 1 else None
            crow["startup_models_by_harness"] = None
        else:
            by_harness: dict[str, list[str]] = {}
            for h in harness_list:
                ml = _ordered_enabled_models(
                    self._model_states.get(h, {}),
                    self._model_ids(h),
                )
                if ml:
                    by_harness[h] = ml
            crow["startup_model"] = None
            crow["startup_models"] = None
            crow["startup_models_by_harness"] = by_harness or None
        return crow
