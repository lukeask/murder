"""Ink launch path (`murder.app.cli.tui_cmd`).

Covers the launcher preconditions and entrypoint resolution without spawning Node or the daemon:
- Node missing / too old → clear `InkLaunchError`, no spawn.
- Node >= floor → no raise.
- Dev probe (inktui/src/index.tsx present) selects `tsx`; absent node_modules is a distinct error.
- Installed probe selects `node <_inktui/index.js>`.
- The spawn sets the sole application WebSocket URL.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from murder.app.cli import tui_cmd
from murder.app.cli.tui_cmd import (
    MIN_NODE_MAJOR,
    InkLaunchError,
    _require_node,
    _resolve_ink_entrypoint,
    _spawn_ink,
)


def _make_dev_checkout(repo: Path, *, with_node_modules: bool = True) -> None:
    src = repo / "inktui" / "src"
    src.mkdir(parents=True)
    (src / "index.tsx").write_text("// dev entry\n", encoding="utf-8")
    if with_node_modules:
        bin_dir = repo / "inktui" / "node_modules" / ".bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "tsx").write_text("#!/bin/sh\n", encoding="utf-8")


# --- Node runtime check ---------------------------------------------------------------------------


def _stub_node_version(monkeypatch, *, output: str | None, returncode: int = 0) -> None:
    """Stub `subprocess.run(["node", "--version"])`. output=None → FileNotFoundError (absent)."""

    def _fake_run(cmd, *args, **kwargs):
        assert cmd[0] == "node"
        if output is None:
            raise FileNotFoundError("node")
        return SimpleNamespace(returncode=returncode, stdout=output, stderr="")

    monkeypatch.setattr(tui_cmd.subprocess, "run", _fake_run)


def test_node_missing_raises_and_does_not_spawn(monkeypatch):
    _stub_node_version(monkeypatch, output=None)
    with pytest.raises(InkLaunchError) as exc:
        _require_node()
    assert "Node" in str(exc.value)
    assert "none" in str(exc.value)


def test_node_too_old_raises(monkeypatch):
    _stub_node_version(monkeypatch, output="v16.20.0\n")
    with pytest.raises(InkLaunchError) as exc:
        _require_node()
    assert f">= {MIN_NODE_MAJOR}" in str(exc.value)
    assert "16" in str(exc.value)


def test_node_recent_enough_ok(monkeypatch):
    _stub_node_version(monkeypatch, output=f"v{MIN_NODE_MAJOR}.5.1\n")
    _require_node()  # no raise


def test_node_nonzero_exit_treated_as_unusable(monkeypatch):
    _stub_node_version(monkeypatch, output="garbage", returncode=1)
    with pytest.raises(InkLaunchError):
        _require_node()


# --- entrypoint resolution ------------------------------------------------------------------------


def test_dev_probe_selects_tsx(repo_root: Path):
    _make_dev_checkout(repo_root)
    argv, cwd = _resolve_ink_entrypoint(repo_root)
    assert argv[-1] == "src/index.tsx"
    assert "tsx" in argv[0]  # the inktui/node_modules/.bin/tsx binary
    assert cwd == repo_root / "inktui"


def test_dev_probe_missing_node_modules_is_distinct_error(repo_root: Path):
    _make_dev_checkout(repo_root, with_node_modules=False)
    with pytest.raises(InkLaunchError) as exc:
        _resolve_ink_entrypoint(repo_root)
    assert "node_modules" in str(exc.value)


def test_installed_probe_selects_node_bundle(repo_root: Path, monkeypatch, tmp_path: Path):
    # No source checkout in repo_root → falls through to the packaged bundle.
    bundle = tmp_path / "_inktui" / "index.js"
    bundle.parent.mkdir(parents=True)
    bundle.write_text("// bundle\n", encoding="utf-8")

    def _fake_files(pkg: str):
        assert pkg == "murder"
        return tmp_path

    monkeypatch.setattr(tui_cmd, "files", _fake_files)
    argv, cwd = _resolve_ink_entrypoint(repo_root)
    assert argv[0] == "node"
    assert argv[1] == str(bundle)
    assert cwd is None


def test_installed_probe_missing_bundle_raises(repo_root: Path, monkeypatch, tmp_path: Path):
    def _fake_files(pkg: str):
        return tmp_path  # no _inktui/index.js under here

    monkeypatch.setattr(tui_cmd, "files", _fake_files)
    with pytest.raises(InkLaunchError):
        _resolve_ink_entrypoint(repo_root)


# --- spawn sets the application endpoint ----------------------------------------------------------


def test_spawn_sets_application_websocket_env(monkeypatch):
    captured: dict[str, object] = {}

    def _fake_run(argv, *args, **kwargs):
        captured["argv"] = argv
        captured["env"] = kwargs.get("env")
        captured["cwd"] = kwargs.get("cwd")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(tui_cmd.subprocess, "run", _fake_run)
    websocket_url = "ws://127.0.0.1:9001/api/ws"
    rc = _spawn_ink(["node", "/x/index.js"], None, websocket_url, "murder")

    assert rc == 0
    assert captured["argv"] == ["node", "/x/index.js"]
    assert captured["env"]["MURDER_APPLICATION_WS_URL"] == websocket_url
    # The repo name rides along via MURDER_PROJECT for the top-bar branding.
    assert captured["env"]["MURDER_PROJECT"] == "murder"
    assert captured["cwd"] is None


def test_spawn_passes_cwd_for_dev(monkeypatch):
    captured: dict[str, object] = {}

    def _fake_run(argv, *args, **kwargs):
        captured["cwd"] = kwargs.get("cwd")
        return SimpleNamespace(returncode=3)

    monkeypatch.setattr(tui_cmd.subprocess, "run", _fake_run)
    expected_rc = 3
    rc = _spawn_ink(["tsx", "src/index.tsx"], Path("/repo/inktui"), "ws://127.0.0.1:9001/api/ws", "murder")
    assert rc == expected_rc
    assert captured["cwd"] == "/repo/inktui"
