"""Doctor command: environment sanity checks."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import typer

from murder.config import Config
from murder.harnesses import REGISTRY
from murder.config import HarnessRoleConfig
from murder.storage.filesystem import read_lock_pid
from murder.storage.paths import agents_dir, db_path, lock_path


def _repo_root() -> Path:
    return Path.cwd().resolve()


def _pid_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _configured_harnesses(role_cfg: HarnessRoleConfig) -> list[str]:
    harnesses = [role_cfg.harness]
    if role_cfg.harnesses:
        harnesses.extend(role_cfg.harnesses)
    return list(dict.fromkeys(harnesses))


def _harness_executable(kind: str, role_cfg: HarnessRoleConfig) -> str:
    if role_cfg.binary and kind == role_cfg.harness:
        return role_cfg.binary
    cmd = REGISTRY[kind]().startup_cmd(Path("."))
    if not cmd:
        raise ValueError("empty startup command")
    return cmd[0]


def _validate_configured_harness_binaries(cfg: Config) -> list[str]:
    issues: list[str] = []
    for role_name, role_cfg in (
        ("collaborator", cfg.collaborator),
        ("default_crow", cfg.default_crow),
    ):
        for kind in _configured_harnesses(role_cfg):
            try:
                exe = _harness_executable(kind, role_cfg)
            except KeyError:
                issues.append(f"{role_name} harness {kind}: unknown harness")
                continue
            except Exception as e:
                issues.append(f"{role_name} harness {kind}: startup command unavailable ({e})")
                continue
            if shutil.which(exe) is None:
                issues.append(f"{role_name} harness {kind}: {exe} not on PATH")
    return issues


def cmd_doctor() -> None:
    """Sanity-check environment and config."""
    repo = _repo_root()
    issues: list[str] = []
    if shutil.which("tmux") is None:
        issues.append("tmux not found on PATH")
    if shutil.which("git") is None:
        issues.append("git not found on PATH")
    else:
        p = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            check=False,
            text=True,
        )
        if p.returncode != 0 or p.stdout.strip() != "true":
            issues.append("not a git checkout; `murder kick` requires git diff checks")
        else:
            head = subprocess.run(
                ["git", "-C", str(repo), "rev-parse", "HEAD"],
                capture_output=True,
                check=False,
                text=True,
            )
            if head.returncode != 0:
                issues.append(
                    "git repo has no commits yet; make an initial commit before `murder kick`"
                )
    try:
        cfg = Config.load(repo)
    except Exception as e:
        issues.append(f"config load failed: {e}")
    else:
        if cfg.project.name == "TODO_SET_ME":
            issues.append("project.name is TODO_SET_ME; run `murder config`")
        issues.extend(_validate_configured_harness_binaries(cfg))
    if not os.environ.get("OPENROUTER_API_KEY"):
        issues.append("OPENROUTER_API_KEY unset (CrowHandler/Sentinel need it)")
    if not agents_dir(repo).exists():
        issues.append(".murder/ missing — run murder init")
    elif not db_path(repo).exists():
        issues.append("murder.db missing — run murder init")
    lock = lock_path(repo)
    if lock.exists():
        pid = read_lock_pid(lock)
        if pid is None:
            issues.append(f"lock file exists but has no readable PID: {lock}")
        elif _pid_is_alive(pid):
            issues.append(f"another murder runtime is running here (PID {pid} in {lock})")
        else:
            issues.append(f"stale murder lock for dead PID {pid}: run `murder down`")
    if issues:
        for i in issues:
            typer.secho(f"- {i}", fg=typer.colors.YELLOW, err=True)
        raise typer.Exit(1)
    typer.secho("doctor: OK", fg=typer.colors.GREEN)
