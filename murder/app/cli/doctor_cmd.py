"""Doctor command: environment + config preflight checks.

Prints a ✓/✗ line per check and exits 0 if all pass, 1 if any fail. Matches the
plain-text output style of the rest of the CLI (no rich tables); typer is only
used for the exit code.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import typer

from murder.app.cli._util import node_major_version as _node_major_version
from murder.app.cli._util import pid_is_alive as _pid_is_alive
from murder.app.cli._util import repo_root as _repo_root
from murder.config import Config, HarnessRoleConfig
from murder.llm.harnesses import REGISTRY
from murder.state.persistence.schema import get_db, init_db
from murder.state.storage.filesystem import read_lock_pid
from murder.state.storage.paths import agents_dir, db_path, lock_path

# Provider env vars that count as "an LLM key is configured" (Groq/Cerebras first).
_LLM_KEY_ENV_VARS = (
    "GROQ_API_KEY",
    "CEREBRAS_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
)

_MIN_NODE_MAJOR = 20


def _ok(msg: str) -> None:
    typer.echo(f"✓ {msg}")


def _fail(msg: str) -> None:
    typer.echo(f"✗ {msg}")


def _warn(msg: str) -> None:
    typer.echo(f"! {msg}")


def _configured_harnesses(role_cfg: HarnessRoleConfig) -> list[str]:
    harnesses = [role_cfg.harness]
    if role_cfg.harnesses:
        harnesses.extend(role_cfg.harnesses)
    return list(dict.fromkeys(harnesses))


def _harness_binary(kind: str, role_cfg: HarnessRoleConfig) -> str:
    """Resolve the executable a harness kind shells out to."""
    if role_cfg.binary and kind == role_cfg.harness:
        return role_cfg.binary
    cmd = REGISTRY[kind]().startup_cmd(Path("."))
    if not cmd:
        raise ValueError("empty startup command")
    return cmd[0]


def _check_tmux(failures: list[str]) -> None:
    if shutil.which("tmux") is not None:
        _ok("tmux found")
        return
    _fail("tmux not found")
    typer.echo("    install: macOS `brew install tmux` / Ubuntu `sudo apt install tmux`")
    failures.append("tmux")


def _check_node(failures: list[str]) -> None:
    major = _node_major_version()
    if major is None:
        _fail("node 20+ required (found: none)")
        typer.echo("    install: use NVM (https://github.com/nvm-sh/nvm) or https://nodejs.org")
        failures.append("node")
        return
    if major < _MIN_NODE_MAJOR:
        _fail(f"node 20+ required (found: v{major})")
        typer.echo("    install: use NVM (https://github.com/nvm-sh/nvm) or https://nodejs.org")
        failures.append("node")
        return
    _ok(f"node v{major}")


def _check_git(repo: Path, failures: list[str]) -> None:
    if shutil.which("git") is None:
        _fail("git not found")
        failures.append("git")
        return
    inside = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        check=False,
        text=True,
    )
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        _fail("not a git checkout (git diff checks require a repo)")
        failures.append("git")
        return
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        check=False,
        text=True,
    )
    if head.returncode != 0:
        _fail("git repo has no commits yet (make an initial commit)")
        failures.append("git")
        return
    _ok("git repo OK")


def _check_config_and_harnesses(repo: Path, failures: list[str]) -> Config | None:
    try:
        cfg = Config.load(repo)
    except Exception as e:
        _fail(f"config load failed: {e}")
        failures.append("config")
        return None
    _ok("config loads")

    for role_name, role_cfg in (
        ("collaborator", cfg.collaborator),
        ("default_crow", cfg.default_crow),
    ):
        for kind in _configured_harnesses(role_cfg):
            try:
                binary = _harness_binary(kind, role_cfg)
            except KeyError:
                _fail(f"{role_name} harness {kind}: unknown harness")
                failures.append(f"harness:{kind}")
                continue
            except Exception as e:
                _fail(f"{role_name} harness {kind}: startup command unavailable ({e})")
                failures.append(f"harness:{kind}")
                continue
            if shutil.which(binary) is None:
                _fail(f"{role_name} harness {kind}: `{binary}` not on PATH")
                failures.append(f"harness:{kind}")
            else:
                _ok(f"{role_name} harness {kind}: `{binary}` found")
    return cfg


def _check_api_keys() -> None:
    present = [name for name in _LLM_KEY_ENV_VARS if os.environ.get(name)]
    if present:
        _ok(f"LLM API key(s) set: {', '.join(present)}")
    else:
        _warn(
            "no LLM API key found (set GROQ_API_KEY, CEREBRAS_API_KEY, or another "
            "provider key before launching)"
        )


def _check_db(repo: Path, failures: list[str]) -> None:
    path = db_path(repo)
    if not path.exists():
        _warn("no murder.db yet (run `murder init`)")
        return
    try:
        conn = get_db(path)
        try:
            init_db(conn)
        finally:
            conn.close()
    except Exception as e:
        _fail(f"DB migration failed: {e}")
        failures.append("db")
        return
    _ok("DB healthy")


def _check_lock(repo: Path) -> None:
    lock = lock_path(repo)
    if not lock.exists():
        return
    pid = read_lock_pid(lock)
    if pid is None:
        _warn(f"lock file exists but has no readable PID: {lock}")
        return
    if _pid_is_alive(pid):
        _warn(f"another murder runtime is running here (PID {pid})")
        return
    _warn(f"stale lock file found (dead PID {pid}) — safe to delete: {lock}")


def cmd_doctor() -> None:
    """Sanity-check environment, config, and prerequisites."""
    repo = _repo_root()
    failures: list[str] = []

    _check_tmux(failures)
    _check_node(failures)
    _check_git(repo, failures)
    _check_config_and_harnesses(repo, failures)
    _check_api_keys()
    _check_db(repo, failures)
    _check_lock(repo)

    typer.echo("")
    if failures:
        typer.echo(f"doctor: {len(failures)} check(s) failed")
        raise typer.Exit(1)
    typer.echo("doctor: all checks passed")
