"""Per-host env isolation: Config.load must not mutate os.environ; repo env is overlay-only."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from murder.config import (
    Config,
    apply_daemon_env,
    load_repo_env,
    merge_subprocess_env,
    project_env_path,
)


def _write_roles(repo: Path) -> None:
    murder = repo / ".murder"
    murder.mkdir(parents=True, exist_ok=True)
    (murder / "roles.yaml").write_text("project:\n  name: env-iso\n", encoding="utf-8")


def test_config_load_does_not_apply_project_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("PROJECT_SECRET_KEY", raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_roles(repo)
    project_env_path(repo).write_text("PROJECT_SECRET_KEY=from-project\n", encoding="utf-8")
    (repo / ".env").write_text("ROOT_SECRET=from-root\n", encoding="utf-8")

    Config.load(repo)

    assert "PROJECT_SECRET_KEY" not in os.environ
    assert "ROOT_SECRET" not in os.environ


def test_load_repo_env_merges_project_then_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    repo = tmp_path / "repo"
    repo.mkdir()
    murder = repo / ".murder"
    murder.mkdir()
    (murder / ".env").write_text("SHARED=project\nONLY_PROJECT=1\n", encoding="utf-8")
    (repo / ".env").write_text("SHARED=root\nONLY_ROOT=1\n", encoding="utf-8")

    env = load_repo_env(repo)
    assert env["SHARED"] == "root"
    assert env["ONLY_PROJECT"] == "1"
    assert env["ONLY_ROOT"] == "1"
    assert "SHARED" not in os.environ or os.environ.get("SHARED") != "root"


def test_activating_second_repo_does_not_clobber_process_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simulate two activates: process environ stays daemon-baseline."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("DAEMON_BASE", "keep-me")
    monkeypatch.delenv("REPO_A_KEY", raising=False)
    monkeypatch.delenv("REPO_B_KEY", raising=False)

    repo_a = tmp_path / "a"
    repo_b = tmp_path / "b"
    for repo, key, val in (
        (repo_a, "REPO_A_KEY", "aaa"),
        (repo_b, "REPO_B_KEY", "bbb"),
    ):
        repo.mkdir()
        _write_roles(repo)
        project_env_path(repo).write_text(f"{key}={val}\n", encoding="utf-8")

    apply_daemon_env()
    env_a = load_repo_env(repo_a)
    Config.load(repo_a)
    env_b = load_repo_env(repo_b)
    Config.load(repo_b)

    assert os.environ.get("DAEMON_BASE") == "keep-me"
    assert "REPO_A_KEY" not in os.environ
    assert "REPO_B_KEY" not in os.environ

    child_a = merge_subprocess_env(env_a)
    child_b = merge_subprocess_env(env_b)
    assert child_a["REPO_A_KEY"] == "aaa"
    assert "REPO_B_KEY" not in child_a
    assert child_b["REPO_B_KEY"] == "bbb"
    assert "REPO_A_KEY" not in child_b
    assert child_a["DAEMON_BASE"] == "keep-me"
    assert child_b["DAEMON_BASE"] == "keep-me"


def test_merge_subprocess_env_overlays_without_mutating_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SHARED", "daemon")
    monkeypatch.setenv("ONLY_DAEMON", "1")
    before = dict(os.environ)
    merged = merge_subprocess_env({"SHARED": "repo", "ONLY_REPO": "1"})
    assert merged["SHARED"] == "repo"
    assert merged["ONLY_REPO"] == "1"
    assert merged["ONLY_DAEMON"] == "1"
    assert os.environ["SHARED"] == "daemon"
    assert "ONLY_REPO" not in os.environ
    assert dict(os.environ) == before
