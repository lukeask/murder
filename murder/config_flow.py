"""Interactive `murder --config` / `murder config`."""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any, cast

import typer
import yaml

from murder.config import Config, HarnessKind, HarnessRoleConfig
from murder.harnesses import REGISTRY
from murder.storage.paths import agents_dir, roles_yaml

_HARNESS_ROWS: list[tuple[HarnessKind, str, str]] = [
    ("cursor", "Cursor CLI", "agent"),
    ("claude_code", "Claude Code", "claude"),
    ("codex", "Codex CLI", "codex"),
    ("pi", "Pi", "pi"),
    ("murder_native", "Murder native (stub)", "murder_native"),
]

_API_MODEL_ROWS: list[tuple[str, str]] = [
    ("anthropic/claude-opus-4-7", "Claude Opus 4.7"),
    ("anthropic/claude-sonnet-4-6", "Claude Sonnet 4.6"),
    ("anthropic/claude-haiku-4-5", "Claude Haiku 4.5"),
    ("openai/gpt-5.5", "GPT-5.5"),
    ("openai/gpt-5.4", "GPT-5.4"),
    ("openai/gpt-5.4-mini", "GPT-5.4 Mini"),
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


def _dedupe_strings(xs: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in xs:
        val = str(x).strip()
        if val and val not in seen:
            seen.add(val)
            out.append(val)
    return out


def _current_harnesses(monkey: dict[str, Any]) -> list[HarnessKind]:
    raw_pool = monkey.get("harnesses")
    if isinstance(raw_pool, list) and raw_pool:
        return _dedupe_preserve(
            [cast(HarnessKind, x) for x in raw_pool if isinstance(x, str)]
        )
    harness = monkey.get("harness")
    return [cast(HarnessKind, harness)] if isinstance(harness, str) else ["cursor"]


def _toggle_harnesses(current: list[HarnessKind], idxs: list[int]) -> list[HarnessKind]:
    selected = set(current)
    ordered = [kind for kind, _, _ in _HARNESS_ROWS]
    for idx in idxs:
        kind = _HARNESS_ROWS[idx - 1][0]
        if kind in selected:
            selected.remove(kind)
        else:
            selected.add(kind)
    return [kind for kind in ordered if kind in selected]


def _parse_model_tokens(line: str, models: list[tuple[str, str]]) -> list[str] | None:
    s = line.strip()
    if not s:
        return None
    out: list[str] = []
    for token in re.split(r"[\s,;]+", s):
        if not token:
            continue
        try:
            idx = int(token)
        except ValueError:
            out.append(token)
            continue
        if idx < 1 or idx > len(models):
            return []
        out.append(models[idx - 1][0])
    return out


def _model_rows_for_harness(harness: HarnessKind) -> list[tuple[str, str]]:
    return list(REGISTRY[harness].available_startup_models)


def _existing_models_for_harness(
    monkey: dict[str, Any], harness: HarnessKind, primary_harness: HarnessKind
) -> list[str]:
    by_harness = monkey.get("startup_models_by_harness")
    if isinstance(by_harness, dict):
        raw = by_harness.get(harness)
        if isinstance(raw, list):
            return _dedupe_strings([str(x) for x in raw])
    if harness != primary_harness:
        return []
    pool = monkey.get("startup_models")
    if isinstance(pool, list):
        return _dedupe_strings([str(x) for x in pool])
    model = monkey.get("startup_model")
    return [str(model)] if model else []


def _apply_model_choices(monkey: dict[str, Any], harnesses: list[HarnessKind]) -> None:
    selected_by_harness: dict[HarnessKind, list[str]] = {}
    primary_harness = harnesses[0]
    for harness in harnesses:
        choices = _model_rows_for_harness(harness)
        if not choices:
            typer.echo(f"\n{harness} models: no runtime model list yet; leaving unset.")
            continue

        current = _existing_models_for_harness(monkey, harness, primary_harness)
        typer.echo(f"\n{harness} models (numbers select listed models; custom ids are allowed):")
        for i, (model, label) in enumerate(choices, start=1):
            mark = "x" if model in current else " "
            typer.echo(f"  [{mark}] {i}. {label} ({model})")
        if current:
            typer.echo(f"Current for {harness}: {', '.join(current)}")
        line = input(f"{harness} models: ").strip()
        parsed = _parse_model_tokens(line, choices)
        if parsed is None:
            selected_by_harness[harness] = current
            typer.echo(
                f"Keeping {harness} models: {', '.join(current) if current else '(unset)'}"
            )
        elif not parsed:
            typer.secho(
                f"No valid model entries for {harness}; unchanged.",
                fg=typer.colors.YELLOW,
                err=True,
            )
            selected_by_harness[harness] = current
            typer.echo(
                f"Keeping {harness} models: {', '.join(current) if current else '(unset)'}"
            )
        else:
            selected_by_harness[harness] = _dedupe_strings(parsed)
            typer.secho(
                f"Selected {harness} models: {', '.join(selected_by_harness[harness])}",
                fg=typer.colors.GREEN,
            )

    selected_by_harness = {k: v for k, v in selected_by_harness.items() if v}
    primary_models = selected_by_harness.get(harnesses[0], [])
    monkey["startup_model"] = primary_models[0] if primary_models else None
    if len(harnesses) == 1:
        monkey["startup_models"] = primary_models if len(primary_models) > 1 else None
        monkey["startup_models_by_harness"] = None
    else:
        monkey["startup_models"] = None
        monkey["startup_models_by_harness"] = selected_by_harness or None


def _parse_single_model_token(line: str, models: list[tuple[str, str]]) -> str | None:
    parsed = _parse_model_tokens(line, models)
    if parsed is None:
        return None
    if len(parsed) != 1:
        return ""
    return parsed[0]


def _role_model(raw: dict[str, Any], cfg: Config, role: str) -> str:
    role_raw = raw.get(role)
    if isinstance(role_raw, dict) and role_raw.get("model"):
        return str(role_raw["model"])
    if role == "sentinel":
        return cfg.sentinel.model
    if role == "augur":
        return cfg.augur.model
    raise KeyError(role)


def _apply_api_model_choice(
    raw: dict[str, Any],
    cfg: Config,
    role: str,
    label: str,
) -> None:
    current = _role_model(raw, cfg, role)
    typer.echo(f"\n{label} model ({role})")
    for i, (model, name) in enumerate(_API_MODEL_ROWS, start=1):
        mark = "x" if model == current else " "
        typer.echo(f"  [{mark}] {i}. {name} ({model})")
    typer.echo(f"Current {role} model: {current}")
    line = input(f"{label} model: ").strip()
    selected = _parse_single_model_token(line, _API_MODEL_ROWS)
    if selected is None:
        typer.echo(f"Keeping {role} model: {current}")
        return
    if not selected:
        typer.secho(
            f"No valid model entry for {role}; unchanged.",
            fg=typer.colors.YELLOW,
            err=True,
        )
        typer.echo(f"Keeping {role} model: {current}")
        return

    role_cfg = raw.get(role)
    if not isinstance(role_cfg, dict):
        role_cfg = getattr(cfg, role).model_dump(mode="python")
        raw[role] = role_cfg
    role_cfg["model"] = selected
    typer.secho(f"Selected {role} model: {selected}", fg=typer.colors.GREEN)


def _apply_api_role_choices(raw: dict[str, Any], cfg: Config) -> None:
    _apply_api_model_choice(raw, cfg, "sentinel", "Planner")
    _apply_api_model_choice(raw, cfg, "augur", "Notetaker")


def _print_summary(raw: dict[str, Any], cfg: Config, monkey: dict[str, Any]) -> None:
    harnesses = _current_harnesses(monkey)
    typer.echo("\nCurrent selection")
    typer.echo(f"  Monkey harnesses: {', '.join(harnesses)}")
    by_harness = monkey.get("startup_models_by_harness")
    if isinstance(by_harness, dict) and by_harness:
        for harness in harnesses:
            models = by_harness.get(harness) or []
            typer.echo(f"  {harness} models: {', '.join(models) if models else '(unset)'}")
    else:
        models = monkey.get("startup_models") or (
            [monkey["startup_model"]] if monkey.get("startup_model") else []
        )
        typer.echo(f"  Monkey models: {', '.join(models) if models else '(unset)'}")
    typer.echo(f"  Planner model: {_role_model(raw, cfg, 'sentinel')}")
    typer.echo(f"  Notetaker model: {_role_model(raw, cfg, 'augur')}")


def _prompt_yes_no(prompt: str) -> bool:
    return input(prompt).strip().lower() in {"y", "yes"}


def run_guided_config(repo: Path) -> None:
    """Edit `.agents/roles.yaml` default_monkey harness/model pools."""
    path = roles_yaml(repo)
    if not agents_dir(repo).exists() or not path.exists():
        typer.secho(
            f"Missing {path} — run murder init in this repo first.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)

    raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cfg = Config.load(repo)
    monkey: dict[str, Any] = cfg.default_monkey.model_dump(mode="python")

    typer.echo("Murder defaults")
    typer.echo(
        "Tickets without overrides pick stably from the selected harness/model pools."
    )
    typer.echo("Press Enter to keep any current value.\n")
    project = raw.get("project")
    if not isinstance(project, dict):
        project = {}
        raw["project"] = project
    current_name = str(project.get("name") or cfg.project.name)
    name = input(f"Project name [{current_name}]: ").strip()
    if name:
        project["name"] = name

    while True:
        current_harnesses = _current_harnesses(monkey)
        typer.echo("Harnesses (toggle by number, space or comma; empty = leave unchanged):")
        for i, (kind, label, exe) in enumerate(_HARNESS_ROWS, start=1):
            mark = "x" if kind in current_harnesses else " "
            typer.echo(f"  [{mark}] {i}. {kind} - {label} ({exe}: {_which_note(exe)})")
        typer.echo(f"Current harness pool: {', '.join(str(x) for x in current_harnesses)}")

        choice = input("Indices: ").strip()
        idxs = _parse_indices(choice, len(_HARNESS_ROWS))
        if idxs is not None:
            if not idxs:
                typer.secho(
                    "No valid indices; harness section unchanged.",
                    fg=typer.colors.YELLOW,
                    err=True,
                )
            else:
                toggled = _toggle_harnesses(current_harnesses, idxs)
                if not toggled:
                    typer.secho(
                        "At least one harness must remain selected; unchanged.",
                        fg=typer.colors.YELLOW,
                        err=True,
                    )
                else:
                    current_harnesses = toggled
                    monkey["harness"] = current_harnesses[0]
                    monkey["harnesses"] = (
                        current_harnesses if len(current_harnesses) > 1 else None
                    )
                    typer.secho(
                        f"Selected harnesses: {', '.join(current_harnesses)}",
                        fg=typer.colors.GREEN,
                    )

        _apply_model_choices(monkey, current_harnesses)
        _apply_api_role_choices(raw, cfg)
        _print_summary(raw, cfg, monkey)
        if not _prompt_yes_no("\nEdit another config pass? [y/N]: "):
            break
        typer.echo("")

    try:
        validated = HarnessRoleConfig.model_validate(monkey)
    except Exception as e:
        typer.secho(f"Invalid default_monkey config: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from e

    raw["default_monkey"] = validated.model_dump(mode="python", exclude_none=True)
    path.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")
    typer.secho(f"Wrote {path}", fg=typer.colors.GREEN)
