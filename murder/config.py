"""Config loading: roles.yaml + .env."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from importlib import resources
from pathlib import Path
from typing import Any, Literal, TypeAlias, cast

import yaml
from pydantic import BaseModel, Field, field_validator

HarnessKind: TypeAlias = Literal["cursor", "claude_code", "codex", "pi", "murder_native"]

try:  # python-dotenv is in dependencies but tests may stub
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    def load_dotenv(*args: Any, **kwargs: Any) -> bool:  # type: ignore[misc]
        return False


class ProjectConfig(BaseModel):
    name: str
    repo_path: Path = Path(".")


class HarnessRoleConfig(BaseModel):
    kind: Literal["harness"] = "harness"
    harness: HarnessKind
    harnesses: list[HarnessKind] | None = Field(
        default=None,
        description=(
            "Pool of harness kinds; tickets without harness override pick stably by ticket id."
        ),
    )
    startup_model: str | None = None
    startup_models: list[str] | None = Field(
        default=None,
        description=(
            "Pool of startup model strings; tickets without model override pick "
            "stably by ticket id."
        ),
    )
    startup_models_by_harness: dict[HarnessKind, list[str]] | None = Field(
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
        cls, v: dict[HarnessKind, list[str]] | None
    ) -> dict[HarnessKind, list[str]] | None:
        if v is None:
            return None
        out: dict[HarnessKind, list[str]] = {}
        for harness, models in v.items():
            cleaned = [str(x).strip() for x in models if str(x).strip()]
            if cleaned:
                out[harness] = cleaned
        return out or None


class ApiRoleConfig(BaseModel):
    kind: Literal["api"] = "api"
    provider: Literal["openrouter", "anthropic", "openai", "local"] = "openrouter"
    model: str
    max_context_tokens: int = 180_000


class AugurConfig(ApiRoleConfig):
    poll_interval_s: float = 45.0
    forced_summary_every_n_ticks: int = 7
    stuck_threshold_ticks: int = 3
    context_lines: int = 40


class SentinelConfig(ApiRoleConfig):
    tools: list[str] = Field(
        default_factory=lambda: [
            "read_file",
            "grep",
            "list_tickets",
            "read_ticket",
            "send_to_monkey",
            "escalate_user",
            "escalate_collaborator",
            "append_sentinel_note",
            "pause_ticket",
        ]
    )


class TuiConfig(BaseModel):
    ticket_grid_max_rows: int = 20
    pane_mirror_height: int = 30
    escalation_strip_height: int = 8
    refresh_ms: int = 1000


class RuntimeConfig(BaseModel):
    run_dir: Path = Path(".agents/runs")
    session_name_template: str = "murder_{project}_{role}{suffix}"


class Config(BaseModel):
    project: ProjectConfig
    collaborator: HarnessRoleConfig
    sentinel: SentinelConfig
    augur: AugurConfig
    default_monkey: HarnessRoleConfig
    tui: TuiConfig = TuiConfig()
    runtime: RuntimeConfig = RuntimeConfig()

    @classmethod
    def load(cls, repo_root: Path) -> Config:
        # Load env: global first, then project-local overrides.
        load_dotenv(env_path(), override=False)
        load_dotenv(project_env_path(repo_root), override=True)
        load_dotenv(repo_root / ".env", override=True)

        bundled = _load_bundled_defaults()
        project = repo_root / ".agents" / "roles.yaml"
        merged: dict[str, Any] = bundled
        if project.exists():
            with project.open("r", encoding="utf-8") as f:
                user_yaml = yaml.safe_load(f) or {}
            merged = _deep_merge(bundled, user_yaml)
        return cls.model_validate(merged)


def env_path() -> Path:
    """Global env file; respects XDG_CONFIG_HOME."""
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "murder" / ".env"


def project_env_path(repo_root: Path) -> Path:
    """Project env file created by `murder init`."""
    return repo_root / ".agents" / ".env"


def _load_bundled_defaults() -> dict[str, Any]:
    text = (
        resources.files("murder.templates").joinpath("roles.yaml").read_text(encoding="utf-8")
    )
    return yaml.safe_load(text) or {}


def _deep_merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in over.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def stable_bucket_index(key: str, modulo: int) -> int:
    """Deterministic index for spreading work across a pool (same process or not)."""
    if modulo <= 0:
        raise ValueError("modulo must be positive")
    digest = hashlib.blake2b(key.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") % modulo


def resolve_default_monkey_harness(
    monkey_cfg: HarnessRoleConfig, ticket_row: Mapping[str, Any] | None
) -> HarnessKind:
    overt = (ticket_row or {}).get("harness")
    if overt:
        return cast(HarnessKind, overt)
    pool = list(monkey_cfg.harnesses) if monkey_cfg.harnesses else [monkey_cfg.harness]
    tid = str((ticket_row or {}).get("id") or "")
    return pool[stable_bucket_index(tid, len(pool))]


def resolve_default_monkey_startup_model(
    monkey_cfg: HarnessRoleConfig,
    ticket_row: Mapping[str, Any] | None,
    harness: HarnessKind | None = None,
) -> str | None:
    overt = (ticket_row or {}).get("model")
    if overt:
        return str(overt)
    if harness and monkey_cfg.startup_models_by_harness:
        pool = monkey_cfg.startup_models_by_harness.get(harness)
        if pool:
            tid = str((ticket_row or {}).get("id") or "")
            return pool[stable_bucket_index(tid, len(pool))]
    if monkey_cfg.startup_models:
        pool = monkey_cfg.startup_models
        tid = str((ticket_row or {}).get("id") or "")
        return pool[stable_bucket_index(tid, len(pool))]
    return monkey_cfg.startup_model
