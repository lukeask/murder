"""Interactive `murder --config` / `murder config` (Monkey defaults only for now)."""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

import typer
import yaml

from murder.config import HarnessKind, HarnessRoleConfig, Config
from murder.storage.paths import agents_dir, roles_yaml

_HARNESS_ROWS: list[tuple[HarnessKind, str, str]] = [
    ("cursor", "Cursor CLI", "agent"),
    ("claude_code", "Claude Code", "claude"),
    ("codex", "Codex CLI", "codex"),
    ("pi", "Pi", "pi"),
    ("murder_native", "Murder native (stub)", "murder_native"),
]


def _which_note(exe: str) -> str:
    return "on PATH" if shutil.which(exe) else "not on PATH"


def _parse_indices(line: str, n: int) -> list[int] | None:
    s = line.strip()
    if not s:
        return None
    parts = re.split(r"[\s,;]+", s)
    out: list[int] = []
    for p in parts:
        if not p:
            continue
        try:
            i = int(p)
        except ValueError:
            return []
        if i < 1 or i > n:
            return []
        out.append(i)
    return out


def _dedupe_preserve(xs: list[HarnessKind]) -> list[HarnessKind]:
    seen: set[str] = set()
    out: list[HarnessKind] = []
    for x in xs:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def run_guided_config(repo: Path) -> None:
    """Edit `.agents/roles.yaml` default_monkey harness/model pools."""
    path = roles_yaml(repo)
    if not agents_dir(repo).exists() or not path.exists():
        typer.secho(f"Missing {path} — run murder init in this repo first.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cfg = Config.load(repo)
    monkey: dict[str, Any] = cfg.default_monkey.model_dump(mode="python")

    typer.echo("Monkey (ticket implementer) defaults — multi-select harnesses and models.")
    typer.echo("Each ticket without its own harness/model picks stably from the pool.\n")

    typer.echo("Harnesses (toggle by number, space or comma; empty = leave unchanged):")
    for i, (kind, label, exe) in enumerate(_HARNESS_ROWS, start=1):
        typer.echo(f"  [{i}] {kind} — {label} ({exe}: {_which_note(exe)})")
    pool_h_list = monkey.get("harnesses")
    if isinstance(pool_h_list, list) and pool_h_list:
        typer.echo(f"Current pool: {', '.join(str(x) for x in pool_h_list)}")
    else:
        typer.echo(f"Current primary harness: {monkey.get('harness')} (no multi pool)")

    choice = input("Indices: ").strip()
    idxs = _parse_indices(choice, len(_HARNESS_ROWS))
    if idxs is not None:
        if not idxs:
            typer.secho("No valid indices; harness section unchanged.", fg=typer.colors.YELLOW, err=True)
        else:
            picked = [_HARNESS_ROWS[i - 1][0] for i in idxs]
            picked_u = _dedupe_preserve(picked)
            monkey["harness"] = picked_u[0]
            monkey["harnesses"] = picked_u if len(picked_u) > 1 else None

    typer.echo("")
    pool_m_list = monkey.get("startup_models")
    if isinstance(pool_m_list, list) and pool_m_list:
        typer.echo(f"Current model pool: {', '.join(str(x) for x in pool_m_list)}")
    elif monkey.get("startup_model"):
        typer.echo(f"Current startup_model: {monkey.get('startup_model')}")
    else:
        typer.echo("Current startup_model: (unset)")
    typer.echo(
        "Model pool: comma-separated ids (e.g. composer, gpt-4.1). "
        "One entry sets only startup_model; two+ set startup_models (+ first as startup_model). "
        "Empty = leave unchanged."
    )
    models_line = input("Models: ").strip()
    if models_line:
        parts = [p.strip() for p in models_line.split(",") if p.strip()]
        if not parts:
            typer.secho("Model section unchanged.", fg=typer.colors.YELLOW, err=True)
        elif len(parts) == 1:
            monkey["startup_model"] = parts[0]
            monkey["startup_models"] = None
        else:
            monkey["startup_model"] = parts[0]
            monkey["startup_models"] = parts

    try:
        validated = HarnessRoleConfig.model_validate(monkey)
    except Exception as e:
        typer.secho(f"Invalid default_monkey config: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from e

    raw["default_monkey"] = validated.model_dump(mode="python", exclude_none=True)
    path.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")
    typer.secho(f"Wrote {path}", fg=typer.colors.GREEN)
