"""User-level config stored under XDG config home.

This is intentionally separate from project `.murder/roles.yaml`: it stores
local UI preferences that should follow the user across repos.

Optional `collaborator`, `default_crow`, and `notetaker` blocks mirror the
shape of `.murder/roles.yaml` sections; they are merged globally before the
project file (see `Config.load`).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

UserHarnessKind = Literal["cursor", "claude_code", "codex", "pi", "antigravity"]


class StartupRogueConfig(BaseModel):
    """The auto-spawned rogue crow seeded on daemon boot — the "Startup Rogue".

    When set, the service ensures exactly one ticketless rogue with this
    harness/model/effort exists on startup, so typing `murder` lands the user in a
    ready-to-type chat against it. ``None`` (the default) = no startup rogue.

    ``model`` empty = let the harness adapter pick its own default; ``effort``
    ``None`` = no reasoning-effort override.
    """

    model_config = ConfigDict(extra="ignore")

    harness: UserHarnessKind = "claude_code"
    model: str = ""
    effort: str | None = None


class TuiUserConfig(BaseModel):
    """User-facing TUI preferences round-tripped by the `settings.{get,update}` RPC pair.

    The frontend's binding registry (`inktui/src/input/bindings.ts`) is the authority on
    `ActionId`s, so `key_overrides` is stored opaquely here (an `ActionId -> key char` map);
    pydantic only validates the closed `modifier`/`theme` shape.

    `extra="ignore"` is load-bearing: a stale `config.yaml` from the old schema (which had
    `tui.editor` and a free-form `tui.theme`) must still load without error — unknown tui keys
    are dropped on read rather than raising.
    """

    model_config = ConfigDict(extra="ignore")

    theme: str = "everforest-dark"
    modifier: Literal["alt", "ctrl", "both"] = "alt"
    # ActionId -> key char. Stored as-is; the TS registry validates ids.
    key_overrides: dict[str, str] = Field(default_factory=dict)
    # Spaces of horizontal gap between adjacent pane borders (rail<->stage, stage panes,
    # rail panes). 0 = flush borders (the default look); capped at 4 (the radio select's range).
    pane_gap: int = Field(default=0, ge=0, le=4)
    # Vim-style editing in the chat input (modal normal/insert + yank/paste). Off by default.
    vim_mode: bool = False
    # The rogue auto-spawned on daemon boot (None = none); see StartupRogueConfig.
    startup_rogue: StartupRogueConfig | None = None


class UserHarnessRolePatch(BaseModel):
    """Partial harness role; fields align with `HarnessRoleConfig` for deep-merge."""

    kind: Literal["harness"] | None = None
    harness: UserHarnessKind | None = None
    harnesses: list[UserHarnessKind] | None = Field(
        default=None,
        description=(
            "Pool of harness kinds; tickets without harness override pick stably by ticket id."
        ),
    )
    startup_model: str | None = None
    startup_effort: str | None = None
    startup_models: list[str] | None = Field(
        default=None,
        description=(
            "Pool of startup model strings; tickets without model override pick "
            "stably by ticket id."
        ),
    )
    startup_models_by_harness: dict[UserHarnessKind, list[str]] | None = Field(
        default=None,
        description=(
            "Per-harness startup model pools; tickets without model override pick "
            "from the pool matching the resolved harness."
        ),
    )
    startup_prompt_template: str | None = None
    binary: str | None = None

    @field_validator("harnesses", "startup_models", mode="before")
    @classmethod
    def _empty_seq_to_none(cls, v: Any) -> Any:
        if v == []:
            return None
        return v

    @field_validator("startup_models", mode="after")
    @classmethod
    def _normalize_model_strings(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        out = [str(x).strip() for x in v if str(x).strip()]
        return out or None

    @field_validator("startup_models_by_harness", mode="after")
    @classmethod
    def _normalize_models_by_harness(
        cls, v: dict[UserHarnessKind, list[str]] | None
    ) -> dict[UserHarnessKind, list[str]] | None:
        if v is None:
            return None
        out: dict[UserHarnessKind, list[str]] = {}
        for harness, models in v.items():
            cleaned = [str(x).strip() for x in models if str(x).strip()]
            if cleaned:
                out[harness] = cleaned
        return out or None


class UserNotetakerPatch(BaseModel):
    """Partial notetaker api role; fields align with `NotetakerConfig` for deep-merge."""

    kind: Literal["api"] | None = None
    provider: Literal["groq", "cerebras", "openrouter", "anthropic", "openai", "local"] | None = None
    model: str | None = None
    max_tokens: int | None = None
    max_context_tokens: int | None = None


class UserLlmProviderSettings(BaseModel):
    """User-scope API credentials/endpoint for one LLM provider.

    Stored in ``config.yaml`` (chmod 0600); applied to the environment via
    ``apply_llm_env`` at daemon start with ``os.environ.setdefault`` semantics
    so env/.env always win.
    """

    model_config = ConfigDict(extra="ignore")

    api_key: str | None = None
    base_url: str | None = None


class UserLlmTier(BaseModel):
    """A named LLM tier: a (provider, model) pair the user can bind roles to."""

    model_config = ConfigDict(extra="ignore")

    provider: Literal["groq", "cerebras", "openrouter", "anthropic", "openai", "local"]
    model: str
    auto_free: bool = False


class UserLlmConfig(BaseModel):
    """User-scope LLM config: provider credentials, named tiers, role->tier map."""

    model_config = ConfigDict(extra="ignore")

    providers: dict[
        Literal["groq", "cerebras", "openrouter", "local"], UserLlmProviderSettings
    ] = Field(default_factory=dict)
    tiers: dict[str, UserLlmTier] = Field(default_factory=dict)
    # role name -> tier name
    roles: dict[str, str] = Field(default_factory=dict)


class UserConfig(BaseModel):
    tui: TuiUserConfig = Field(default_factory=TuiUserConfig)
    # Default rung on the single --log-level ladder (error / warning / info /
    # debug / advanced / advanced-raw); overridable by --log-level and
    # MURDER_LOG_LEVEL. The recorder mode rides the same rung — there is no
    # separate advanced-logging flag (see murder.observability.logging_setup).
    log_level: str = "info"
    collaborator: UserHarnessRolePatch | None = None
    default_crow: UserHarnessRolePatch | None = None
    notetaker: UserNotetakerPatch | None = None
    llm: UserLlmConfig | None = None


# Built-in tiers, available even with no user `llm.tiers`. User-defined tiers of
# the same name override these (see `resolve_tier`).
BUILTIN_TIERS: dict[str, UserLlmTier] = {
    "cheap": UserLlmTier(provider="groq", model="openai/gpt-oss-120b", auto_free=True),
    "smart": UserLlmTier(provider="cerebras", model="openai/gpt-oss-120b"),
}


def resolve_tier(cfg: UserConfig | None, role: str) -> UserLlmTier | None:
    """Resolve *role* to a tier: roles[role] -> tier name -> user tiers, then builtins.

    Returns None when there's no role mapping or the tier name is unknown.
    """
    if cfg is None or cfg.llm is None:
        return None
    tier_name = cfg.llm.roles.get(role)
    if tier_name is None:
        return None
    user_tier = cfg.llm.tiers.get(tier_name)
    if user_tier is not None:
        return user_tier
    return BUILTIN_TIERS.get(tier_name)


# config.yaml provider settings -> environment variable. local has no api-key env
# mapping for api_key beyond LOCAL_OPENAI_API_KEY; see apply_llm_env.
_PROVIDER_ENV_MAP: dict[tuple[str, str], str] = {
    ("groq", "api_key"): "GROQ_API_KEY",
    ("cerebras", "api_key"): "CEREBRAS_API_KEY",
    ("openrouter", "api_key"): "OPENROUTER_API_KEY",
    ("local", "base_url"): "LOCAL_OPENAI_BASE_URL",
    ("local", "api_key"): "LOCAL_OPENAI_API_KEY",
}


def apply_llm_env(user_cfg: UserConfig | None) -> None:
    """Apply config.yaml provider settings to ``os.environ`` with setdefault.

    Only sets a var when it isn't already present (env/.env always win) and the
    config value is non-empty. Testable in isolation; call from ``Config.load``.
    """
    if user_cfg is None or user_cfg.llm is None:
        return
    for provider, settings in user_cfg.llm.providers.items():
        for attr in ("api_key", "base_url"):
            env_name = _PROVIDER_ENV_MAP.get((provider, attr))
            if env_name is None:
                continue
            value = getattr(settings, attr, None)
            if value:
                os.environ.setdefault(env_name, value)


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "murder"


def config_path() -> Path:
    return config_dir() / "config.yaml"


def templates_path() -> Path:
    """Userspace/global text-template registry (follows the user across repos)."""
    return config_dir() / "templates.yaml"


_TEMPLATE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def load_templates(path: Path | None = None) -> list[dict[str, str]]:
    """Read the userspace templates registry.

    Tolerates a missing/empty file or a missing ``templates:`` key by returning
    an empty list. Each record is coerced to ``{"name": str, "body": str}``.
    """
    tpath = path or templates_path()
    if not tpath.exists():
        return []
    try:
        raw = yaml.safe_load(tpath.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(raw, dict):
        return []
    records = raw.get("templates")
    if not isinstance(records, list):
        return []
    out: list[dict[str, str]] = []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        out.append({"name": str(rec.get("name", "")), "body": str(rec.get("body", ""))})
    return out


def _normalize_templates(records: Any) -> list[dict[str, str]]:
    """Validate/coerce records: drop invalid names, de-dupe (last wins), sort."""
    by_name: dict[str, str] = {}
    if isinstance(records, list):
        for rec in records:
            if not isinstance(rec, dict):
                continue
            name = str(rec.get("name", ""))
            if not _TEMPLATE_NAME_RE.match(name):
                continue
            by_name[name] = str(rec.get("body", ""))
    return [{"name": n, "body": by_name[n]} for n in sorted(by_name)]


def save_templates(records: Any, path: Path | None = None) -> list[dict[str, str]]:
    """Normalize and atomically persist the templates registry.

    Returns the normalized list (canonical state) so callers can sync to it.
    """
    normalized = _normalize_templates(records)
    tpath = path or templates_path()
    tpath.parent.mkdir(parents=True, exist_ok=True)
    payload = yaml.safe_dump({"templates": normalized}, default_flow_style=False, sort_keys=False)
    tmp = tpath.with_suffix(".tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(tpath)
    return normalized


def workflows_path() -> Path:
    """Userspace/global workflow-definition registry (follows the user across repos)."""
    return config_dir() / "workflows.yaml"


def load_workflows(path: Path | None = None) -> list[dict[str, Any]]:
    """Read the userspace workflow registry as a list of raw definition dicts.

    Tolerant by design: a missing/empty file, a non-dict top level, or a missing
    ``workflows:`` key all yield ``[]``, and non-dict entries are skipped. Records
    are returned verbatim (no coercion) — ``save_workflows`` is the only writer and
    already emits canonical ``WorkflowDef`` dumps, so readers can trust the shape.
    """
    wpath = path or workflows_path()
    if not wpath.exists():
        return []
    try:
        raw = yaml.safe_load(wpath.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(raw, dict):
        return []
    records = raw.get("workflows")
    if not isinstance(records, list):
        return []
    return [rec for rec in records if isinstance(rec, dict)]


def _normalize_workflows(records: Any) -> list[dict[str, Any]]:
    """Coerce records through ``WorkflowDef``, drop invalid, de-dupe (last wins), sort.

    A record is dropped if pydantic rejects its shape OR ``validate_workflow``
    reports a graph/name problem — the registry never persists a definition that
    can't later be materialized. Kept defs are re-serialized from the model so the
    stored form is canonical regardless of the caller's input dict.
    """
    # Imported lazily to avoid a user_config -> work.workflows import cycle and to
    # keep this module importable in contexts that never touch workflows.
    from murder.work.workflows import WorkflowDef, validate_workflow

    by_name: dict[str, dict[str, Any]] = {}
    if isinstance(records, list):
        for rec in records:
            if not isinstance(rec, dict):
                continue
            try:
                defn = WorkflowDef.model_validate(rec)
            except Exception:  # noqa: BLE001
                continue
            if validate_workflow(defn):
                continue
            by_name[defn.name] = defn.model_dump(mode="json")
    return [by_name[n] for n in sorted(by_name)]


def save_workflows(records: Any, path: Path | None = None) -> list[dict[str, Any]]:
    """Normalize and atomically persist the workflow registry.

    Returns the normalized canonical list so callers can sync to it.
    """
    normalized = _normalize_workflows(records)
    wpath = path or workflows_path()
    wpath.parent.mkdir(parents=True, exist_ok=True)
    payload = yaml.safe_dump({"workflows": normalized}, default_flow_style=False, sort_keys=False)
    tmp = wpath.with_suffix(".tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(wpath)
    return normalized


def spawn_favorites_path(path: Path | None = None) -> Path:
    """Userspace/global spawn-wizard favorite presets (follows the user across repos)."""
    return config_dir() / "spawn_favorites.yaml"


# Max number of spawn-favorite presets persisted; older entries past this are dropped.
_MAX_SPAWN_FAVORITES = 10


def load_spawn_favorites(path: Path | None = None) -> list[dict[str, str]]:
    """Read the userspace spawn-favorites registry.

    Tolerates a missing/empty/unparseable file or a missing ``favorites:`` key by
    returning an empty list. Each record is coerced to
    ``{"name": str, "harness": str, "model": str, "effort": str}``.
    """
    tpath = path or spawn_favorites_path()
    if not tpath.exists():
        return []
    try:
        raw = yaml.safe_load(tpath.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(raw, dict):
        return []
    records = raw.get("favorites")
    if not isinstance(records, list):
        return []
    out: list[dict[str, str]] = []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        out.append(
            {
                "name": str(rec.get("name", "")),
                "harness": str(rec.get("harness", "")),
                "model": str(rec.get("model", "")),
                "effort": str(rec.get("effort", "")),
            }
        )
    return out


def _normalize_spawn_favorites(records: Any) -> list[dict[str, str]]:
    """Validate/coerce favorites: drop blank names, preserve order, clamp to the cap.

    Order is user-meaningful (unlike templates), so it is preserved; de-dupe is not
    performed. Records are clamped to the first ``_MAX_SPAWN_FAVORITES``.
    """
    out: list[dict[str, str]] = []
    if isinstance(records, list):
        for rec in records:
            if not isinstance(rec, dict):
                continue
            name = str(rec.get("name", "")).strip()
            if not name:
                continue
            out.append(
                {
                    "name": name,
                    "harness": str(rec.get("harness", "")),
                    "model": str(rec.get("model", "")),
                    "effort": str(rec.get("effort", "")),
                }
            )
    return out[:_MAX_SPAWN_FAVORITES]


def save_spawn_favorites(records: Any, path: Path | None = None) -> list[dict[str, str]]:
    """Normalize and atomically persist the spawn-favorites registry.

    Returns the normalized list (canonical state) so callers can sync to it.
    """
    normalized = _normalize_spawn_favorites(records)
    tpath = path or spawn_favorites_path()
    tpath.parent.mkdir(parents=True, exist_ok=True)
    payload = yaml.safe_dump({"favorites": normalized}, default_flow_style=False, sort_keys=False)
    tmp = tpath.with_suffix(".tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(tpath)
    return normalized


_GATED_HARNESS = "native_coding_crow"


def _scrub_gated_harness(raw: dict[str, Any]) -> None:
    """Drop user-scope references to a gated-out harness, in place.

    User config must never brick loading: rather than raise on a stale
    ``native_coding_crow`` reference, we silently drop the offending entry from
    the ``collaborator`` / ``default_crow`` patch blocks.
    """
    for block_name in ("collaborator", "default_crow"):
        block = raw.get(block_name)
        if not isinstance(block, dict):
            continue
        # scalar harness key
        if block.get("harness") == _GATED_HARNESS:
            block.pop("harness", None)
        # list harness pool
        pool = block.get("harnesses")
        if isinstance(pool, list):
            filtered = [h for h in pool if h != _GATED_HARNESS]
            if filtered:
                block["harnesses"] = filtered
            else:
                block.pop("harnesses", None)
        # per-harness startup model pools (dict keyed by harness)
        by_harness = block.get("startup_models_by_harness")
        if isinstance(by_harness, dict):
            by_harness.pop(_GATED_HARNESS, None)
            if not by_harness:
                block.pop("startup_models_by_harness", None)


def load_user_config(path: Path | None = None) -> UserConfig:
    cfg_path = path or config_path()
    if not cfg_path.exists():
        return UserConfig()
    with cfg_path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    if not isinstance(raw, dict):
        raw = {}
    _scrub_gated_harness(raw)
    return UserConfig.model_validate(raw)


def save_user_config(config: UserConfig, path: Path | None = None) -> None:
    cfg_path = path or config_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = config.model_dump(mode="json", exclude_none=True)
    with cfg_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=True)
    # config.yaml holds API keys: keep it owner-only on every write.
    os.chmod(cfg_path, 0o600)
