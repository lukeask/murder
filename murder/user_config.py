"""User-level config stored under XDG config home.

This is intentionally separate from project `.murder/roles.yaml`: it stores
local UI preferences that should follow the user across repos.

Optional `collaborator`, `planner`, `default_crow`, and `notetaker` blocks mirror the
shape of `.murder/roles.yaml` sections. Harness/model selection fields
(`UserHarnessRolePatch`: harness, harnesses, startup_model, startup_effort,
startup_models, startup_models_by_harness) are user-scope ONLY — project
roles.yaml is ignored for them in `Config.load` (bundled defaults are the only
fallback). Other fields merge bundled -> user -> project.
"""

from __future__ import annotations

import json
import os
import re
from importlib import resources
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

UserHarnessKind = Literal["cursor", "claude_code", "codex", "pi", "antigravity"]
UserLlmProviderKind = Literal[
    "groq",
    "cerebras",
    "openrouter",
    "anthropic",
    "openai",
    "local",
    "lemonade",
    "openai_compatible",
]
ModelSource = Literal["recommended", "discovered", "custom"]
CandidateLocality = Literal["local", "remote", "unknown"]
CandidateCostClass = Literal["free", "paid", "unknown"]


class BarWidgetUserConfig(BaseModel):
    """One bar widget's persisted enable/placement (Phase 3.1)."""

    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    placement: Literal["top", "bottom"] = "bottom"
    adaptive: bool = True
    # Usage widget only: harness ids feeding the min-timer. None/[] = all harnesses.
    harnesses: list[UserHarnessKind] | None = Field(default=None)

    @field_validator("harnesses", mode="before")
    @classmethod
    def _empty_harnesses_to_none(cls, v: Any) -> Any:
        if v == []:
            return None
        return v


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
    # Number of virtual workspaces (1 = feature inert; capped at 9 for direct-jump keybindings).
    workspace_count: int = Field(default=1, ge=1, le=9)
    # Vim-style editing in the chat input (modal normal/insert + yank/paste). Off by default.
    vim_mode: bool = False
    # Default chat view mode for panes with no per-pane override (TUIchat-3). Only verbose/condensed
    # are settable here; `tmux` is reachable in the TUI only via the per-pane cycle key, never a default.
    default_chat_view_mode: Literal["verbose", "condensed"] = "verbose"
    # Document source interpretation is explicit and never inferred from its filename.
    document_display_mode: Literal["plain", "markdown"] = "plain"
    # The rogue auto-spawned on daemon boot (None = none); see StartupRogueConfig.
    startup_rogue: StartupRogueConfig | None = None
    # Per-widget top/bottom bar configuration (enable + placement). Omitted keys use registry defaults.
    bar_widgets: dict[str, BarWidgetUserConfig] = Field(default_factory=dict)


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


class UserLlmMetadata(BaseModel):
    """Optional metadata defaults; model values override provider values."""

    model_config = ConfigDict(extra="ignore")

    locality: CandidateLocality | None = None
    cost_class: CandidateCostClass | None = None
    tags: set[str] | None = None
    capabilities: set[str] | None = None
    execution_modes: set[str] | None = None
    context_window: int | None = Field(default=None, ge=1)


class UserLlmModelOverride(UserLlmMetadata):
    """Per-model catalog enablement and metadata overrides."""

    enabled: bool | None = None


class UserLlmModelCatalog(BaseModel):
    """A provider instance's source catalog plus non-destructive adjustments."""

    model_config = ConfigDict(extra="ignore")

    source: ModelSource = "recommended"
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)
    overrides: dict[str, UserLlmModelOverride] = Field(default_factory=dict)


class UserLlmProviderAuth(BaseModel):
    model_config = ConfigDict(extra="ignore")

    api_key: str | None = None


class UserLlmProviderSettings(BaseModel):
    """User-scope API credentials/endpoint for one LLM provider.

    Stored in ``config.yaml`` (chmod 0600); applied to the environment via
    ``apply_llm_env`` at daemon start with ``os.environ.setdefault`` semantics
    so env/.env always win.
    """

    model_config = ConfigDict(extra="ignore")

    # ``api_key`` and ``base_url`` are retained as compatibility accessors for
    # the legacy provider map.  New config persists ``auth`` and ``endpoint``.
    type: UserLlmProviderKind | None = None
    name: str | None = None
    enabled: bool = True
    endpoint: str | None = None
    auth: UserLlmProviderAuth = Field(default_factory=UserLlmProviderAuth)
    metadata: UserLlmMetadata = Field(default_factory=UserLlmMetadata)
    models: UserLlmModelCatalog = Field(default_factory=UserLlmModelCatalog)

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_shape(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        legacy_type = data.pop("provider", None)
        if legacy_type is not None and "type" not in data:
            data["type"] = legacy_type
        legacy_key = data.pop("api_key", None)
        if legacy_key is not None:
            auth = dict(data.get("auth") or {})
            auth.setdefault("api_key", legacy_key)
            data["auth"] = auth
        legacy_endpoint = data.pop("base_url", None)
        if legacy_endpoint is not None and "endpoint" not in data:
            data["endpoint"] = legacy_endpoint
        # The preliminary/legacy map ``models: {id: {enabled: ...}}`` is a
        # custom catalog.  Convert it without discarding user choices.
        models = data.get("models")
        if isinstance(models, dict) and "source" not in models:
            data["models"] = {
                "source": "custom",
                "include": list(models),
                "overrides": models,
            }
        return data

    @property
    def api_key(self) -> str | None:
        return self.auth.api_key

    @property
    def base_url(self) -> str | None:
        return self.endpoint


class UserLlmExactCandidate(BaseModel):
    """One pinned provider-instance/model pair in a selection policy."""

    model_config = ConfigDict(extra="ignore")

    provider: str
    model: str


class UserLlmSelectorMatch(BaseModel):
    model_config = ConfigDict(extra="ignore")

    locality: CandidateLocality | None = None
    cost_class: CandidateCostClass | None = None
    tags: set[str] = Field(default_factory=set)
    capabilities: set[str] = Field(default_factory=set)


class UserLlmSelector(BaseModel):
    model_config = ConfigDict(extra="ignore")

    match: UserLlmSelectorMatch | None = None
    candidate: UserLlmExactCandidate | None = None


class UserLlmPolicyGroup(BaseModel):
    """Equal-priority selectors, rotated by the stateful policy resolver."""

    model_config = ConfigDict(extra="ignore")

    selectors: list[UserLlmSelector] = Field(default_factory=list)


class UserLlmPolicy(BaseModel):
    """Reusable ordered model-selection policy.

    Candidates in the first group are preferred.  Later groups are fallbacks;
    the resolver rotates within each group without changing group priority.
    """

    model_config = ConfigDict(extra="ignore")

    builtin: bool = False
    name: str | None = None
    groups: list[UserLlmPolicyGroup] = Field(default_factory=list)


BUILTIN_LLM_POLICIES: dict[str, UserLlmPolicy] = {
    "local-then-free": UserLlmPolicy(
        builtin=True,
        name="Local Then Free",
        groups=[
            UserLlmPolicyGroup(selectors=[UserLlmSelector(match=UserLlmSelectorMatch(locality="local"))]),
            UserLlmPolicyGroup(selectors=[UserLlmSelector(match=UserLlmSelectorMatch(locality="remote", cost_class="free"))]),
        ],
    ),
    "remote-free": UserLlmPolicy(
        builtin=True,
        name="Remote Free",
        groups=[UserLlmPolicyGroup(selectors=[UserLlmSelector(match=UserLlmSelectorMatch(locality="remote", cost_class="free"))])],
    ),
    "local-only": UserLlmPolicy(
        builtin=True,
        name="Local Only",
        groups=[UserLlmPolicyGroup(selectors=[UserLlmSelector(match=UserLlmSelectorMatch(locality="local"))])],
    ),
    # Oracle-oriented default: prefer capable remote free models, then any local.
    # Execution mode (batch vs immediate) is intentionally not encoded here.
    "oracle-smart": UserLlmPolicy(
        builtin=True,
        name="Oracle Smart",
        groups=[
            UserLlmPolicyGroup(
                selectors=[
                    UserLlmSelector(
                        match=UserLlmSelectorMatch(
                            locality="remote",
                            cost_class="free",
                            capabilities={"tools"},
                        )
                    )
                ]
            ),
            UserLlmPolicyGroup(selectors=[UserLlmSelector(match=UserLlmSelectorMatch(locality="local"))]),
            UserLlmPolicyGroup(
                selectors=[UserLlmSelector(match=UserLlmSelectorMatch(locality="remote", cost_class="free"))]
            ),
        ],
    ),
}


ExecutionMode = Literal["immediate", "batch"]


class UserExecutionPolicy(BaseModel):
    """How a request should be submitted and awaited (distinct from model selection)."""

    model_config = ConfigDict(extra="ignore")

    builtin: bool = False
    name: str | None = None
    # Single-mode policies use ``mode``; preferred/fallback policies use the pair.
    mode: ExecutionMode | None = None
    preferred_mode: ExecutionMode | None = None
    fallback_mode: ExecutionMode | None = None

    @model_validator(mode="after")
    def _require_mode_or_preferred(self) -> UserExecutionPolicy:
        if self.mode is None and self.preferred_mode is None:
            raise ValueError("execution policy requires mode or preferred_mode")
        return self


BUILTIN_EXECUTION_POLICIES: dict[str, UserExecutionPolicy] = {
    "immediate": UserExecutionPolicy(builtin=True, name="Immediate", mode="immediate"),
    "batch-preferred": UserExecutionPolicy(
        builtin=True,
        name="Batch Preferred",
        preferred_mode="batch",
        fallback_mode="immediate",
    ),
    "batch-only": UserExecutionPolicy(builtin=True, name="Batch Only", mode="batch"),
}


class UserExecutionConfig(BaseModel):
    """Reusable execution policies, separate from model-selection policies."""

    model_config = ConfigDict(extra="ignore")

    policies: dict[str, UserExecutionPolicy] = Field(default_factory=dict)

    def resolved_policy(self, name: str) -> UserExecutionPolicy | None:
        if name in BUILTIN_EXECUTION_POLICIES:
            return BUILTIN_EXECUTION_POLICIES[name]
        policy = self.policies.get(name)
        return None if policy is None or policy.builtin else policy


class UserOracleConfig(BaseModel):
    """Oracle is a workflow feature, not a provider type (§13.1)."""

    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    model_policy: str = "oracle-smart"
    execution_policy: str = "batch-preferred"


class UserLlmTier(BaseModel):
    """A named LLM tier: a (provider, model) pair the user can bind roles to."""

    model_config = ConfigDict(extra="ignore")

    provider: Literal["groq", "cerebras", "openrouter", "anthropic", "openai", "local"]
    model: str
    auto_free: bool = False


class UserLlmConfig(BaseModel):
    """User-scope direct-LLM config.

    ``tiers`` and ``roles`` are the legacy role-tier interface and remain fully
    supported.  Provider instance IDs, catalogs, and policies are additive.
    """

    model_config = ConfigDict(extra="ignore")

    disabled: bool = False
    # Key is an instance id.  Built-ins conventionally use their provider name
    # (for example ``groq``); custom OpenAI-compatible endpoints use any id.
    providers: dict[str, UserLlmProviderSettings] = Field(default_factory=dict)
    policies: dict[str, UserLlmPolicy] = Field(default_factory=dict)
    # Semantic direct-LLM feature/role name -> reusable policy name.
    feature_policies: dict[str, str] = Field(default_factory=dict)
    # Used when a feature has no explicit policy binding.
    active_policy: str | None = "local-then-free"
    tiers: dict[str, UserLlmTier] = Field(default_factory=dict)
    # role name -> tier name
    roles: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _migrate_pre_policy_schema(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        if "direct_llm_enabled" in data and "disabled" not in data:
            data["disabled"] = not bool(data.pop("direct_llm_enabled"))
        providers = data.get("providers")
        if isinstance(providers, dict):
            migrated: dict[str, Any] = {}
            for provider_id, settings in providers.items():
                if isinstance(settings, dict) and "type" not in settings and "provider" not in settings:
                    settings = {**settings, "type": provider_id}
                migrated[provider_id] = settings
            data["providers"] = migrated
        return data

    def resolved_policy(self, name: str) -> UserLlmPolicy | None:
        """Return an immutable built-in or a user-defined custom policy."""
        if name in BUILTIN_LLM_POLICIES:
            return BUILTIN_LLM_POLICIES[name]
        policy = self.policies.get(name)
        return None if policy is None or policy.builtin else policy


class UserConfig(BaseModel):
    tui: TuiUserConfig = Field(default_factory=TuiUserConfig)
    # Default rung on the single --log-level ladder (error / warning / info /
    # debug / advanced / advanced-raw); overridable by --log-level and
    # MURDER_LOG_LEVEL. The recorder mode rides the same rung — there is no
    # separate advanced-logging flag (see murder.observability.logging_setup).
    log_level: str = "info"
    collaborator: UserHarnessRolePatch | None = None
    planner: UserHarnessRolePatch | None = None
    default_crow: UserHarnessRolePatch | None = None
    notetaker: UserNotetakerPatch | None = None
    llm: UserLlmConfig | None = None
    # Execution policies and Oracle live beside ``llm`` so model-selection
    # config stays free of batch/immediate encoding (§3, §13.2).
    execution: UserExecutionConfig | None = None
    oracle: UserOracleConfig | None = None


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


# ── Theme registry (`themes.yaml`) ────────────────────────────────────────────

_THEME_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")

# Slot keys mirrored from `inktui/src/theme/palettes.ts` — every palette must fill all of them.
_PALETTE_SLOTS: tuple[str, ...] = (
    "bgDim",
    "bg0",
    "bg1",
    "bg2",
    "bg3",
    "bg4",
    "bg5",
    "bgVisual",
    "bgRed",
    "bgGreen",
    "bgBlue",
    "bgYellow",
    "fg",
    "red",
    "orange",
    "yellow",
    "green",
    "aqua",
    "blue",
    "purple",
    "grey0",
    "grey1",
    "grey2",
)


def themes_path(path: Path | None = None) -> Path:
    """Userspace/global theme registry (follows the user across repos)."""
    return config_dir() / "themes.yaml" if path is None else path


def load_builtin_theme_jsons() -> list[dict[str, Any]]:
    """Read bundled palette JSON from ``murder/resources/themes/*.json``."""
    root = resources.files("murder.resources.themes")
    out: list[dict[str, Any]] = []
    for entry in sorted(root.iterdir(), key=lambda p: p.name):
        if not entry.name.endswith(".json"):
            continue
        try:
            raw = json.loads(entry.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if isinstance(raw, dict):
            out.append(raw)
    return out


def _coerce_palette(raw: Any) -> dict[str, str] | None:
    if not isinstance(raw, dict):
        return None
    palette: dict[str, str] = {}
    for slot in _PALETTE_SLOTS:
        value = raw.get(slot)
        if not isinstance(value, str) or not _HEX_COLOR_RE.match(value):
            return None
        palette[slot] = value.lower()
    return palette


def format_theme_record(
    raw: dict[str, Any],
    *,
    builtin: bool,
) -> dict[str, Any] | None:
    """Normalize a theme dict (package JSON or yaml record) into a yaml-ready record."""
    theme_id = str(raw.get("id", "")).strip()
    if not _THEME_ID_RE.match(theme_id):
        return None
    variant = str(raw.get("variant", "")).strip().lower()
    if variant not in ("light", "dark"):
        return None
    palette = _coerce_palette(raw.get("palette"))
    if palette is None:
        return None
    name = str(raw.get("name", "")).strip() or theme_id
    return {
        "id": theme_id,
        "name": name,
        "variant": variant,
        "builtin": builtin,
        "palette": palette,
    }


def format_theme_from_json(json_str: str, theme_id: str | None = None) -> dict[str, Any]:
    """Validate BYO paste JSON and return a user theme record (`builtin: false`).

    Accepts a full wrapper object or a bare palette dict. Raises ``ValueError`` on
    invalid input or duplicate ids.
    """
    try:
        parsed = json.loads(json_str)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("theme JSON must be an object")

    if _coerce_palette(parsed) is not None:
        wrapper: dict[str, Any] = {"palette": parsed, "variant": "dark"}
        if theme_id:
            wrapper["id"] = theme_id
        parsed = wrapper

    if theme_id and not parsed.get("id"):
        parsed = {**parsed, "id": theme_id}
    if not str(parsed.get("id", "")).strip():
        import time

        parsed = {**parsed, "id": f"custom-{int(time.time())}"}
    if not str(parsed.get("variant", "")).strip():
        parsed = {**parsed, "variant": "dark"}

    record = format_theme_record(parsed, builtin=False)
    if record is None:
        raise ValueError("theme JSON is missing required id, variant, or palette slots")

    existing_ids = {t["id"] for t in load_themes()}
    if record["id"] in existing_ids:
        raise ValueError(f"id {record['id']!r} already exists")

    return record


def load_themes(path: Path | None = None) -> list[dict[str, Any]]:
    """Read the userspace theme registry.

    Tolerates a missing/empty/unparseable file or a missing ``themes:`` key by
    returning an empty list.
    """
    tpath = path or themes_path()
    if not tpath.exists():
        return []
    try:
        raw = yaml.safe_load(tpath.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(raw, dict):
        return []
    records = raw.get("themes")
    if not isinstance(records, list):
        return []
    out: list[dict[str, Any]] = []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        builtin = bool(rec.get("builtin", False))
        formatted = format_theme_record(rec, builtin=builtin)
        if formatted is not None:
            out.append(formatted)
    return out


def _normalize_themes(records: Any) -> list[dict[str, Any]]:
    """Validate/coerce theme records: drop invalid, de-dupe (last wins), preserve order.

    Re-injects any bundled themes missing from the caller's list so builtins cannot
    be deleted via ``save_themes``.
    """
    by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    if isinstance(records, list):
        for rec in records:
            if not isinstance(rec, dict):
                continue
            builtin = bool(rec.get("builtin", False))
            formatted = format_theme_record(rec, builtin=builtin)
            if formatted is None:
                continue
            if formatted["id"] not in by_id:
                order.append(formatted["id"])
            by_id[formatted["id"]] = formatted

    for builtin_raw in load_builtin_theme_jsons():
        formatted = format_theme_record(builtin_raw, builtin=True)
        if formatted is None:
            continue
        if formatted["id"] not in by_id:
            order.append(formatted["id"])
            by_id[formatted["id"]] = formatted
        elif by_id[formatted["id"]].get("builtin"):
            # Keep the user's palette for an existing builtin id, but ensure the flag stays true.
            by_id[formatted["id"]]["builtin"] = True

    return [by_id[theme_id] for theme_id in order]


def save_themes(records: Any, path: Path | None = None) -> list[dict[str, Any]]:
    """Normalize and atomically persist the theme registry.

    Returns the normalized list (canonical state) so callers can sync to it.
    """
    normalized = _normalize_themes(records)
    tpath = path or themes_path()
    tpath.parent.mkdir(parents=True, exist_ok=True)
    payload = yaml.safe_dump({"themes": normalized}, default_flow_style=False, sort_keys=False)
    tmp = tpath.with_suffix(".tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(tpath)
    return normalized


def ensure_user_themes(path: Path | None = None) -> bool:
    """Merge any missing bundled themes into the user's ``themes.yaml``.

    Returns ``True`` when the file was created or updated.
    """
    tpath = path or themes_path()
    existing = load_themes(path=tpath)
    existing_ids = {rec["id"] for rec in existing}
    added: list[dict[str, Any]] = []
    for builtin_raw in load_builtin_theme_jsons():
        formatted = format_theme_record(builtin_raw, builtin=True)
        if formatted is None:
            continue
        if formatted["id"] not in existing_ids:
            added.append(formatted)
            existing_ids.add(formatted["id"])
    if not added:
        return False
    merged = existing + added
    save_themes(merged, path=tpath)
    return True


def import_theme_from_json(json_str: str, theme_id: str | None = None) -> tuple[list[dict[str, Any]], str]:
    """Append a BYO theme to ``themes.yaml`` after validation.

    Returns ``(canonical_theme_list, new_theme_id)``.
    """
    record = format_theme_from_json(json_str, theme_id=theme_id)
    merged = load_themes() + [record]
    saved = save_themes(merged)
    return saved, record["id"]


_GATED_HARNESS = "native_coding_crow"


def _scrub_gated_harness(raw: dict[str, Any]) -> None:
    """Drop user-scope references to a gated-out harness, in place.

    User config must never brick loading: rather than raise on a stale
    ``native_coding_crow`` reference, we silently drop the offending entry from
    the ``collaborator`` / ``planner`` / ``default_crow`` patch blocks.
    """
    for block_name in ("collaborator", "planner", "default_crow"):
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
