"""C14 / V-list closure — host-side RPC handlers for V2/V3/V7.

These exercise the stateless filesystem/util handlers registered on the
``ServiceHost`` so the TUI never touches ``.murder/`` or backend imports
directly: image.upload (V2), tui.{load,save}_favorites (V3),
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


def test_image_upload_uses_client_minted_name(repo_root: Path) -> None:
    # F9 contract: the client mints the filename `name` (stem); the server writes
    # `{name}.{ext}` under .murder/images and no longer mints its own.
    host = _host(repo_root)
    payload = base64.b64encode(b"\x89PNG fake bytes").decode("ascii")
    reply = _call(host, "image.upload", {"name": "img-123-abc", "bytes": payload, "ext": "png"})
    assert reply["ok"] is True
    path = Path(reply["path"])
    assert path.exists()
    assert path.name == "img-123-abc.png"
    assert path.read_bytes() == b"\x89PNG fake bytes"
    assert (repo_root / ".murder" / "images") in path.parents


def test_image_upload_rejects_missing_bytes(repo_root: Path) -> None:
    host = _host(repo_root)
    with pytest.raises(ValueError, match="requires base64 bytes"):
        _call(host, "image.upload", {"name": "x"})


def test_image_upload_reports_invalid_base64(repo_root: Path) -> None:
    host = _host(repo_root)
    reply = _call(host, "image.upload", {"name": "x", "bytes": "!!!not base64!!!"})
    assert reply["ok"] is False
    assert "invalid base64" in reply["error"]


def test_image_upload_rejects_empty_name(repo_root: Path) -> None:
    # The server never trusts the wire: an absent/empty (or all-illegal-chars) name
    # is rejected rather than written to a bare-extension path.
    host = _host(repo_root)
    payload = base64.b64encode(b"x").decode("ascii")
    reply = _call(host, "image.upload", {"name": "", "bytes": payload})
    assert reply["ok"] is False
    assert "non-empty name" in reply["error"]
    # All-illegal characters (slashes only) sanitize to empty → same rejection.
    reply2 = _call(host, "image.upload", {"name": "///", "bytes": payload})
    assert reply2["ok"] is False
    assert "non-empty name" in reply2["error"]


def test_image_upload_sanitizes_path_traversal_in_name(repo_root: Path) -> None:
    # A traversal attempt in `name` is scrubbed to a basename — the slashes/dots
    # forming `../` are stripped, so the write stays inside .murder/images.
    host = _host(repo_root)
    payload = base64.b64encode(b"safe").decode("ascii")
    reply = _call(host, "image.upload", {"name": "../../etc/foo", "bytes": payload, "ext": "png"})
    assert reply["ok"] is True
    path = Path(reply["path"])
    images_dir = repo_root / ".murder" / "images"
    assert path.parent == images_dir
    # The separators are gone; only the basename charset survives.
    assert "/" not in path.name
    assert path.name == "....etcfoo.png"
    assert path.exists()


def test_image_upload_sanitizes_ext(repo_root: Path) -> None:
    # `ext` is equally wire-controlled and joined into the path — it is scrubbed too,
    # so a traversal-shaped ext can't escape .murder/images.
    host = _host(repo_root)
    payload = base64.b64encode(b"e").decode("ascii")
    reply = _call(host, "image.upload", {"name": "pic", "bytes": payload, "ext": "../png"})
    assert reply["ok"] is True
    path = Path(reply["path"])
    assert path.parent == (repo_root / ".murder" / "images")
    assert "/" not in path.name
    # `../png` → lstrip('.') → `/png` → sanitize → `png`
    assert path.name == "pic.png"


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
