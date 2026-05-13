"""TUI settings screen — project harness/model config + global theme.

Replaces the old `murder config` / `murder --config` CLI flow.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Literal, TypeAlias, cast

import yaml
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

from murder.config import Config, HarnessKind, HarnessRoleConfig
from murder.harnesses import REGISTRY
from murder.harnesses.model_discovery import discover_harness_models
from murder.storage.paths import roles_yaml
from murder.user_config import UserConfig, save_user_config

_HARNESS_ROWS: list[tuple[HarnessKind, str, str]] = [
    ("cursor", "Cursor CLI", "agent"),
    ("claude_code", "Claude Code", "claude"),
    ("codex", "Codex CLI", "codex"),
    ("pi", "Pi", "pi"),
    ("murder_native", "Murder native", "murder_native"),
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

ModelState: TypeAlias = Literal["disabled", "enabled", "default"]
_MODEL_STATE_ORDER: tuple[ModelState, ...] = ("disabled", "enabled", "default")


def _sid(s: str) -> str:
    """Sanitize a string for use as a Textual widget ID suffix."""
    return "".join(c if c.isalnum() or c == "_" else "_" for c in s)


def _ordered_enabled_models(
    model_states: dict[str, ModelState], model_ids: list[str]
) -> list[str]:
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
    """Settings panel: global (theme) and project (harnesses + models)."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("s", "save", "Save"),
        Binding("g", "scope_global", "Global", show=False),
        Binding("p", "scope_project", "Project", show=False),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("enter", "toggle_item", "Toggle", show=False),
        Binding("space", "toggle_item", "Toggle", show=False),
    ]

    CSS = """
    SettingsScreen {
        align: center middle;
    }
    #settings-box {
        width: 68;
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
    ) -> None:
        super().__init__()
        self._config = config
        self._repo = repo
        self._user_config = user_config
        self._available_themes = available_themes

        # Global state (staged; applied only on save)
        self._theme_sel: str = user_config.tui.theme or (
            available_themes[0] if available_themes else ""
        )

        # Project state
        crow = config.default_crow
        pool: list[HarnessKind] = list(crow.harnesses) if crow.harnesses else [crow.harness]
        self._harnesses: set[HarnessKind] = set(pool)
        configured_models: dict[HarnessKind, list[str]] = {}
        configured_defaults: dict[HarnessKind, str] = {}
        if crow.startup_models_by_harness:
            for k, ms in crow.startup_models_by_harness.items():
                h = cast(HarnessKind, k)
                configured_models[h] = list(ms)
                if ms:
                    configured_defaults[h] = ms[0]
        elif crow.startup_models:
            for h in pool:
                hk = cast(HarnessKind, h)
                configured_models[hk] = list(crow.startup_models)
                if crow.startup_model in crow.startup_models:
                    configured_defaults[hk] = cast(str, crow.startup_model)
        elif crow.startup_model:
            for h in pool:
                hk = cast(HarnessKind, h)
                configured_models[hk] = [crow.startup_model]
                configured_defaults[hk] = crow.startup_model
        self._model_options: dict[HarnessKind, list[tuple[str, str]]] = {}
        self._model_states: dict[HarnessKind, dict[str, ModelState]] = {}
        self._model_discovery_attempted: set[HarnessKind] = set()
        for kind, _, _ in _HARNESS_ROWS:
            options = list(REGISTRY[kind].available_startup_models)
            seen = {model_id for model_id, _ in options}
            for model_id in configured_models.get(kind, []):
                if model_id not in seen:
                    options.append((model_id, model_id))
                    seen.add(model_id)
            self._model_options[kind] = options
            default_model = configured_defaults.get(kind)
            selected = set(configured_models.get(kind, []))
            self._model_states[kind] = {
                model_id: (
                    "default"
                    if model_id == default_model
                    else "enabled"
                    if model_id in selected
                    else "disabled"
                )
                for model_id, _ in options
            }
        self._sentinel_model: str = config.sentinel.model
        self._crow_handler_model: str = config.crow_handler.model
        self._notetaker_model: str = config.notetaker.model
        self._collaborator_harness: HarnessKind = config.collaborator.harness

        self._scope = "project"
        self._cursor_idx = 0
        self._focusable: list[_SettingItem] = []

    # ── compose ────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        with Container(id="settings-box"):
            yield Static("⚙  Settings", id="settings-title")
            yield Static("", id="scope-bar")
            with VerticalScroll(id="scroll"):
                # Global section: theme
                with Vertical(id="section-global"):
                    yield Static(
                        "── THEME  (global: ~/.config/murder/) ──",
                        classes="section-header",
                    )
                    for name in self._available_themes:
                        yield _SettingItem(
                            "radio", name, key=f"theme:{name}", group="theme",
                            checked=(name == self._theme_sel),
                            item_id=f"item-theme-{_sid(name)}",
                        )

                # Project section: harnesses, models, API roles
                with Vertical(id="section-project"):
                    yield Static(
                        "── ENABLED CROW HARNESSES  (project: .murder/) ──",
                        classes="section-header",
                    )
                    for kind, label, exe in _HARNESS_ROWS:
                        avail = "✓" if shutil.which(exe) else "✗"
                        yield _SettingItem(
                            "cb", f"{label}  [{avail}]",
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
                    yield Static(
                        "── COLLABORATOR HARNESS  (project) ──",
                        classes="section-header",
                    )
                    for kind, label, exe in _HARNESS_ROWS:
                        avail = "✓" if shutil.which(exe) else "✗"
                        yield _SettingItem(
                            "radio", f"{label}  [{avail}]",
                            key=f"collab_harness:{kind}", group="collab_harness",
                            checked=(kind == self._collaborator_harness),
                            item_id=f"item-collab_harness-{_sid(kind)}",
                        )
                    yield Static(
                        "── NOTETAKER MODEL  (project) ──",
                        classes="section-header",
                    )
                    for model_id, model_label in _API_MODEL_ROWS:
                        yield _SettingItem(
                            "radio", model_label,
                            key=f"notetaker:{model_id}", group="notetaker",
                            checked=(model_id == self._notetaker_model),
                            item_id=f"item-notetaker-{_sid(model_id)}",
                        )
                    yield Static(
                        "── SENTINEL MODEL  (project) ──",
                        classes="section-header",
                    )
                    for model_id, model_label in _API_MODEL_ROWS:
                        yield _SettingItem(
                            "radio", model_label,
                            key=f"sentinel:{model_id}", group="sentinel",
                            checked=(model_id == self._sentinel_model),
                            item_id=f"item-sentinel-{_sid(model_id)}",
                        )
                    yield Static(
                        "── CROW HANDLER MODEL  (project) ──",
                        classes="section-header",
                    )
                    for model_id, model_label in _API_MODEL_ROWS:
                        yield _SettingItem(
                            "radio", model_label,
                            key=f"crow_handler:{model_id}", group="crow_handler",
                            checked=(model_id == self._crow_handler_model),
                            item_id=f"item-crow_handler-{_sid(model_id)}",
                        )
            yield Static(
                "j/k move  enter/spc toggle  g global  p project  s save  esc cancel",
                id="help-bar",
            )

    def on_mount(self) -> None:
        self._apply_scope()
        self._refresh_model_validation()
        self.run_worker(
            self._refresh_enabled_harness_model_options(),
            exclusive=True,
            group="settings_models",
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

    # ── toggle ─────────────────────────────────────────────────────────────

    def action_toggle_item(self) -> None:
        if not self._focusable or self._cursor_idx >= len(self._focusable):
            return
        item = self._focusable[self._cursor_idx]
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
        elif kind == "sentinel":
            self._sentinel_model = payload
            self._refresh_radio_group("sentinel")
        elif kind == "crow_handler":
            self._crow_handler_model = payload
            self._refresh_radio_group("crow_handler")
        elif kind == "notetaker":
            self._notetaker_model = payload
            self._refresh_radio_group("notetaker")
        elif kind == "collab_harness":
            self._collaborator_harness = cast(HarnessKind, payload)
            self._refresh_radio_group("collab_harness")

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
        self._cursor_idx = next(
            (i for i, f in enumerate(self._focusable) if f.key == saved_key), 0
        )
        self._refresh_cursor()

    def _toggle_model(
        self, item: _SettingItem, kind: HarnessKind, model_id: str
    ) -> None:
        state = self._model_states.setdefault(kind, {}).get(model_id, "disabled")
        next_index = (_MODEL_STATE_ORDER.index(state) + 1) % len(_MODEL_STATE_ORDER)
        next_state = _MODEL_STATE_ORDER[next_index]
        self._model_states[kind][model_id] = next_state
        item.model_state = next_state
        self._refresh_model_validation()

    def _refresh_radio_group(self, group: str) -> None:
        group_value = {
            "theme": self._theme_sel,
            "sentinel": self._sentinel_model,
            "crow_handler": self._crow_handler_model,
            "notetaker": self._notetaker_model,
            "collab_harness": self._collaborator_harness,
        }[group]
        for item in self._focusable:
            if item.group == group:
                item.checked = item.key == f"{group}:{group_value}"

    def _model_widget(self, kind: HarnessKind, model_id: str) -> _SettingItem:
        return self.query_one(
            f"#item-model-{_sid(kind)}-{_sid(model_id)}", _SettingItem
        )

    def _model_item(
        self, kind: HarnessKind, model_id: str, model_label: str
    ) -> _SettingItem:
        return _SettingItem(
            "tri",
            model_label,
            key=f"model:{kind}:{model_id}",
            indent=1,
            model_state=self._model_states[kind].get(model_id, "disabled"),
            item_id=f"item-model-{_sid(kind)}-{_sid(model_id)}",
        )

    def _model_ids(self, kind: HarnessKind) -> list[str]:
        return [model_id for model_id, _ in self._model_options.get(kind, [])]

    def _set_model_section_display(self, kind: HarnessKind, visible: bool) -> None:
        self.query_one(f"#section-models-{_sid(kind)}").display = visible
        for model_id in self._model_ids(kind):
            widget = self._model_widget(kind, model_id)
            widget.model_state = self._model_states.get(kind, {}).get(
                model_id, "disabled"
            )

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
        result = await discover_harness_models(kind, self._repo)
        if not result.ok or not result.data:
            return
        await self._replace_model_options(kind, result.data)

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
        section.display = kind in self._harnesses
        self._refresh_model_validation()
        self._rebuild_focusable()
        if old_key:
            self._cursor_idx = next(
                (i for i, item in enumerate(self._focusable) if item.key == old_key),
                min(self._cursor_idx, max(0, len(self._focusable) - 1)),
            )
        self._refresh_cursor()

    def _dedupe_model_options(
        self, options: list[tuple[str, str]]
    ) -> list[tuple[str, str]]:
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

    # ── save / cancel ──────────────────────────────────────────────────────

    def action_save(self) -> None:
        if not self._save_project():
            return
        self._save_global()
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)

    def _save_global(self) -> None:
        self._user_config.tui.theme = self._theme_sel or None
        save_user_config(self._user_config)

    def _save_project(self) -> bool:
        validation_messages = self._project_validation_messages()
        if validation_messages:
            self._refresh_model_validation()
            self.notify(
                "Invalid model settings; fix red messages before saving.",
                severity="error",
                timeout=5,
            )
            return False

        path = roles_yaml(self._repo)
        if not path.exists():
            self.notify(
                "No .murder/roles.yaml — run murder init first.",
                severity="error",
                timeout=4,
            )
            return False

        raw: dict = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        crow = self._staged_crow_config(raw)

        try:
            HarnessRoleConfig.model_validate(crow)
        except Exception as exc:
            self.notify(f"Invalid config: {exc}", severity="error", timeout=6)
            return False

        raw["default_crow"] = crow
        self._stage_api_role_models(raw)

        path.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")
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

    def _stage_api_role_models(self, raw: dict) -> None:
        sentinel = raw.get("sentinel")
        if not isinstance(sentinel, dict):
            sentinel = {}
        sentinel["model"] = self._sentinel_model
        raw["sentinel"] = sentinel

        crow_handler = raw.get("crow_handler")
        if not isinstance(crow_handler, dict):
            crow_handler = {}
        crow_handler["model"] = self._crow_handler_model
        raw["crow_handler"] = crow_handler

        collaborator = raw.get("collaborator")
        if not isinstance(collaborator, dict):
            collaborator = {}
        collaborator["harness"] = self._collaborator_harness
        raw["collaborator"] = collaborator

        notetaker = raw.get("notetaker")
        if not isinstance(notetaker, dict):
            notetaker = {}
        notetaker["model"] = self._notetaker_model
        raw["notetaker"] = notetaker
