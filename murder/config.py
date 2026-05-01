"""Config loading: roles.yaml + .env."""

from __future__ import annotations

import os
from importlib import resources
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

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
    harness: Literal["cursor", "claude_code", "pi", "murder_native"]
    startup_model: str | None = None
    startup_prompt_template: str | None = None
    binary: str | None = None


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
    def load(cls, repo_root: Path) -> "Config":
        # Load env: global first, then project-local override.
        load_dotenv(env_path(), override=False)
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
