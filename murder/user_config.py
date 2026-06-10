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

UserHarnessKind = Literal[
    "cursor", "claude_code", "codex", "pi", "antigravity", "native_coding_crow"
]


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


class UserConfig(BaseModel):
    tui: TuiUserConfig = Field(default_factory=TuiUserConfig)
    collaborator: UserHarnessRolePatch | None = None
    default_crow: UserHarnessRolePatch | None = None
    notetaker: UserNotetakerPatch | None = None


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "murder"


def config_path() -> Path:
    return config_dir() / "config.yaml"


def load_user_config(path: Path | None = None) -> UserConfig:
    cfg_path = path or config_path()
    if not cfg_path.exists():
        return UserConfig()
    with cfg_path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    if not isinstance(raw, dict):
        raw = {}
    return UserConfig.model_validate(raw)


def save_user_config(config: UserConfig, path: Path | None = None) -> None:
    cfg_path = path or config_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = config.model_dump(mode="json", exclude_none=True)
    with cfg_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=True)
