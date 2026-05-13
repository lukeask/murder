"""User-level config stored under XDG config home.

This is intentionally separate from project `.murder/roles.yaml`: it stores
local UI preferences that should follow the user across repos.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class TuiUserConfig(BaseModel):
    theme: str | None = None
    editor: str | None = None


class UserConfig(BaseModel):
    tui: TuiUserConfig = Field(default_factory=TuiUserConfig)


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
