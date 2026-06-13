"""Tests for `murder doctor` preflight checks."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import typer

from murder.app.cli import doctor_cmd


class _FakeRole:
    """Minimal stand-in for HarnessRoleConfig used by harness-binary resolution."""

    def __init__(self, harness: str = "claude_code") -> None:
        self.harness = harness
        self.harnesses = None
        self.binary = None


class _FakeConfig:
    def __init__(self) -> None:
        self.collaborator = _FakeRole("claude_code")
        self.default_crow = _FakeRole("claude_code")


def _patch_all_green(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Patch every external probe so the all-green path is deterministic."""
    monkeypatch.setattr(doctor_cmd, "_repo_root", lambda: tmp_path)

    def fake_which(name: str) -> str | None:
        return f"/usr/bin/{name}"

    monkeypatch.setattr(doctor_cmd.shutil, "which", fake_which)
    monkeypatch.setattr(doctor_cmd, "_node_major_version", lambda: 22)

    def fake_run(cmd, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        # git rev-parse --is-inside-work-tree / HEAD both succeed.
        return subprocess.CompletedProcess(cmd, 0, stdout="true\n", stderr="")

    monkeypatch.setattr(doctor_cmd.subprocess, "run", fake_run)
    monkeypatch.setattr(doctor_cmd.Config, "load", classmethod(lambda cls, repo: _FakeConfig()))
    monkeypatch.setattr(doctor_cmd, "_harness_binary", lambda kind, role: "claude")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    # No murder.db in tmp_path -> warn only (not a failure). No lock file either.


def test_all_green(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    _patch_all_green(monkeypatch, tmp_path)
    doctor_cmd.cmd_doctor()  # exit 0 -> no exception
    out = capsys.readouterr().out
    assert "✓ tmux found" in out
    assert "✓ node v22" in out
    assert "doctor: all checks passed" in out
    assert "✗" not in out


def test_tmux_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    _patch_all_green(monkeypatch, tmp_path)

    def which_no_tmux(name: str) -> str | None:
        return None if name == "tmux" else f"/usr/bin/{name}"

    monkeypatch.setattr(doctor_cmd.shutil, "which", which_no_tmux)

    with pytest.raises(typer.Exit) as exc:
        doctor_cmd.cmd_doctor()
    assert exc.value.exit_code == 1
    out = capsys.readouterr().out
    assert "✗ tmux not found" in out
    assert "brew install tmux" in out


def test_node_too_old(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    _patch_all_green(monkeypatch, tmp_path)
    monkeypatch.setattr(doctor_cmd, "_node_major_version", lambda: 18)

    with pytest.raises(typer.Exit) as exc:
        doctor_cmd.cmd_doctor()
    assert exc.value.exit_code == 1
    out = capsys.readouterr().out
    assert "✗ node 20+ required (found: v18)" in out


def test_node_absent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    _patch_all_green(monkeypatch, tmp_path)
    monkeypatch.setattr(doctor_cmd, "_node_major_version", lambda: None)

    with pytest.raises(typer.Exit) as exc:
        doctor_cmd.cmd_doctor()
    assert exc.value.exit_code == 1
    out = capsys.readouterr().out
    assert "✗ node 20+ required (found: none)" in out


def test_no_api_key_warns_not_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    _patch_all_green(monkeypatch, tmp_path)
    for var in doctor_cmd._LLM_KEY_ENV_VARS:
        monkeypatch.delenv(var, raising=False)

    doctor_cmd.cmd_doctor()  # warning only -> still exit 0
    out = capsys.readouterr().out
    assert "no LLM API key found" in out
    assert "doctor: all checks passed" in out
