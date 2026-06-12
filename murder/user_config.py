"""User-level config stored under XDG config home.

This is intentionally separate from project `.murder/roles.yaml`: it stores
local UI preferences that should follow the user across repos.

Optional `collaborator`, `default_crow`, and `notetaker` blocks mirror the
shape of `.murder/roles.yaml` sections; they are merged globally before the
project file (see `Config.load`).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

UserHarnessKind = Literal["cursor", "claude_code", "codex", "pi", "antigravity"]


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
    provider: Literal["openrouter", "anthropic", "openai", "local"] | None = None
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

    provider: Literal["openrouter", "anthropic", "openai", "local", "cerebras", "groq"]
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
    collaborator: UserHarnessRolePatch | None = None
    default_crow: UserHarnessRolePatch | None = None
    notetaker: UserNotetakerPatch | None = None
    llm: UserLlmConfig | None = None


# Built-in tiers, available even with no user `llm.tiers`. User-defined tiers of
# the same name override these (see `resolve_tier`).
BUILTIN_TIERS: dict[str, UserLlmTier] = {
    "cheap": UserLlmTier(provider="groq", model="openai/gpt-oss-120b", auto_free=True),
    "smart": UserLlmTier(provider="openrouter", model="anthropic/claude-sonnet-4.5"),
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
