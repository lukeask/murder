"""C14 / V-list closure — host-side RPC handlers for V2/V3/V6/V7.

These exercise the stateless filesystem/util handlers registered on the
``ServiceHost`` so the TUI never touches ``.murder/`` or backend imports
directly: editor.binary (V6), image.upload (V2), tui.{load,save}_favorites (V3),
worktree.list (V7).
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from murder.app.service.host import ServiceHost
from murder.config import (
    Config,
    CrowHandlerConfig,
    HarnessRoleConfig,
    ProjectConfig,
)
from murder.state.storage.paths import tui_prefs_path


def _host(repo_root: Path) -> ServiceHost:
    config = Config(
        project=ProjectConfig(name="repo"),
        collaborator=HarnessRoleConfig(harness="codex"),
        default_crow=HarnessRoleConfig(harness="codex"),
        crow_handler=CrowHandlerConfig(model="test-model"),
    )
    host = ServiceHost(config=config, repo_root=repo_root)
    host.register_default_rpc_handlers()
    return host


def _call(host: ServiceHost, method: str, body: dict) -> dict:
    return host._rpc_handlers[method](body)  # type: ignore[return-value]


def test_editor_binary_prefers_explicit(repo_root: Path) -> None:
    host = _host(repo_root)
    reply = _call(host, "editor.binary", {"preferred": "nvim -p"})
    assert reply["ok"] is True
    assert reply["editor"] == "nvim -p"


def test_editor_binary_falls_back(repo_root: Path, monkeypatch) -> None:
    host = _host(repo_root)
    monkeypatch.setenv("EDITOR", "emacs")
    reply = _call(host, "editor.binary", {})
    assert reply["editor"] == "emacs"


def test_image_upload_stores_under_murder_images(repo_root: Path) -> None:
    host = _host(repo_root)
    payload = base64.b64encode(b"\x89PNG fake bytes").decode("ascii")
    reply = _call(host, "image.upload", {"bytes": payload, "ext": "png"})
    assert reply["ok"] is True
    path = Path(reply["path"])
    assert path.exists()
    assert path.read_bytes() == b"\x89PNG fake bytes"
    assert (repo_root / ".murder" / "images") in path.parents


def test_image_upload_rejects_missing_bytes(repo_root: Path) -> None:
    host = _host(repo_root)
    with pytest.raises(ValueError, match="requires base64 bytes"):
        _call(host, "image.upload", {})


def test_image_upload_reports_invalid_base64(repo_root: Path) -> None:
    host = _host(repo_root)
    reply = _call(host, "image.upload", {"bytes": "!!!not base64!!!"})
    assert reply["ok"] is False
    assert "invalid base64" in reply["error"]


def test_favorites_round_trip(repo_root: Path) -> None:
    host = _host(repo_root)
    # Empty when no prefs file yet.
    assert _call(host, "tui.load_favorites", {})["favorites"] == []
    save = _call(host, "tui.save_favorites", {"favorites": ["crow-b", "crow-a", "crow-a"]})
    assert save["ok"] is True
    assert save["favorites"] == ["crow-a", "crow-b"]
    # Persisted under .murder/tui_prefs.json.
    prefs = tui_prefs_path(repo_root)
    assert prefs.exists()
    assert json.loads(prefs.read_text())["favorites"] == ["crow-a", "crow-b"]
    # Reload reflects the saved set.
    loaded = _call(host, "tui.load_favorites", {})["favorites"]
    assert sorted(loaded) == ["crow-a", "crow-b"]


def test_save_favorites_rejects_non_list(repo_root: Path) -> None:
    host = _host(repo_root)
    with pytest.raises(ValueError, match="requires favorites list"):
        _call(host, "tui.save_favorites", {"favorites": "nope"})


def test_worktree_list_returns_serializable_entries(repo_root: Path, monkeypatch) -> None:
    from murder.state.storage.worktrees import WorktreeEntry

    host = _host(repo_root)
    wt_path = repo_root / ".murder" / "worktrees" / "x"
    fake = [WorktreeEntry(path=wt_path, branch="feat/x", is_main=False)]
    monkeypatch.setattr(
        "murder.state.storage.worktrees.list_murder_worktrees_sync",
        lambda _root: fake,
    )
    reply = _call(host, "worktree.list", {})
    assert reply["ok"] is True
    assert reply["entries"] == [
        {
            "path": str(repo_root / ".murder" / "worktrees" / "x"),
            "branch": "feat/x",
            "is_main": False,
        }
    ]
    # JSON-serializable (no Path objects leak).
    json.dumps(reply)
