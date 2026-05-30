"""Config loading: roles.yaml + .env."""

from __future__ import annotations

import os
from collections.abc import Mapping
from importlib import resources
from pathlib import Path
from typing import Any, Literal, TypeAlias, cast

import yaml
from pydantic import BaseModel, Field, field_validator

HarnessKind: TypeAlias = Literal[
    "cursor", "claude_code", "codex", "pi", "antigravity", "native_coding_crow"
]

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
    startup_effort: str | None = None
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
    provider: Literal["openrouter", "anthropic", "openai", "local", "cerebras", "groq"] = "openrouter"
    auto_free: bool = False
    model: str
    max_context_tokens: int = 180_000


class CrowHandlerConfig(ApiRoleConfig):
    poll_interval_s: float = 45.0
    forced_summary_every_n_ticks: int = 7
    stuck_threshold_ticks: int = 3
    context_lines: int = 40


class PlannerConfig(BaseModel):
    """Per-plan planning agent: a tmux-backed harness whose cwd is `.murder/`."""

    kind: Literal["harness"] = "harness"
    harness: HarnessKind = "claude_code"
    startup_model: str | None = None
    startup_effort: str | None = None
    startup_prompt_template: str = "planner.md"
    # The crow-ASK relay template used by PlanningHandler.
    crow_ask_template: str = "crow_ask_to_planner.md"
    # PlanningHandler poll cadence (parallel to CrowHandlerConfig.poll_interval_s).
    poll_interval_s: float = 5.0

    @field_validator("kind", mode="before")
    @classmethod
    def _legacy_api_kind_to_harness(cls, v: Any) -> Any:
        if v == "api":
            return "harness"
        return v


class NotetakerConfig(ApiRoleConfig):
    """Planning-mode "notetaker": tidies the user's stream-of-consciousness
    into a clean notes doc via read/write tools.

    Defaults to Cerebras/zai-glm-4.7 (reasoning model, fast on Cerebras
    hardware). Falls back gracefully to no-LLM behavior if CEREBRAS_API_KEY
    is unset — same degradation path as any API role with a missing key.
    """

    provider: Literal["openrouter", "anthropic", "openai", "local", "cerebras", "groq"] = "cerebras"
    model: str = "zai-glm-4.7"
    max_tokens: int = 1500


class TuiConfig(BaseModel):
    ticket_grid_max_rows: int = 20
    pane_mirror_height: int = 30
    escalation_strip_height: int = 8
    refresh_ms: int = 1000


class RuntimeConfig(BaseModel):
    run_dir: Path = Path(".murder/runs")
    session_name_template: str = "murder_{project}_{role}{suffix}"


class Config(BaseModel):
    project: ProjectConfig
    collaborator: HarnessRoleConfig
    notetaker: NotetakerConfig = NotetakerConfig()
    planner: PlannerConfig = PlannerConfig()
    crow_handler: CrowHandlerConfig
    default_crow: HarnessRoleConfig
    tui: TuiConfig = TuiConfig()
    runtime: RuntimeConfig = RuntimeConfig()

    @classmethod
    def load(cls, repo_root: Path) -> Config:
        # Load env: global first, then project-local overrides.
        load_dotenv(env_path(), override=False)
        load_dotenv(project_env_path(repo_root), override=True)
        load_dotenv(repo_root / ".env", override=True)

        bundled = _load_bundled_defaults()
        from murder.user_config import load_user_config

        merged: dict[str, Any] = _deep_merge(
            dict(bundled), load_user_config().model_dump(mode="json", exclude_none=True)
        )
        project = repo_root / ".murder" / "roles.yaml"
        if project.exists():
            with project.open("r", encoding="utf-8") as f:
                user_yaml = yaml.safe_load(f) or {}
            merged = _deep_merge(merged, user_yaml)
        return cls.model_validate(merged)


def env_path() -> Path:
    """Global env file; respects XDG_CONFIG_HOME."""
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "murder" / ".env"


def project_env_path(repo_root: Path) -> Path:
    """Project env file created by `murder init`."""
    return repo_root / ".murder" / ".env"


def _load_bundled_defaults() -> dict[str, Any]:
    text = resources.files("murder.templates").joinpath("roles.yaml").read_text(encoding="utf-8")
    return yaml.safe_load(text) or {}


def _deep_merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in over.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


from murder.policy.harness_resolution import (  # noqa: E402
    resolve_default_crow_harness,
    resolve_default_crow_startup_effort,
    resolve_default_crow_startup_model,
    stable_bucket_index,
)
