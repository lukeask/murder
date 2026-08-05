"""Unit tests for RepositoryManager activate / deactivate / list / idle eviction."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from murder.app.service import repository_manager as mgr_mod
from murder.app.service.repository_manager import RepositoryManager
from murder.llm.harnesses.versioning import HarnessVersionRegistry
from murder.state.persistence.connection import connect, resolve_repository
from murder.state.persistence.schema import init_db


def _fake_host(repo_root: Path, repository_id: str, *, live_agents: bool = False):
    host = MagicMock()
    host.repo_root = repo_root
    host.repository_id = repository_id
    host.has_live_agents.return_value = live_agents
    host.background_tasks = None
    host.supervisor = None
    host.clear_shutdown_signal = MagicMock()
    host.start = AsyncMock()
    host.stop = AsyncMock()
    return host


@pytest.fixture
def isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_file = tmp_path / "murder.db"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    # db_path() reads config_dir(); point shared db at our temp file via env.
    from murder.state.storage import paths as paths_mod

    monkeypatch.setattr(paths_mod, "db_path", lambda: db_file)
    monkeypatch.setattr(mgr_mod, "connect", lambda: connect(db_file))
    return db_file


async def test_activate_reuses_existing_host(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    repo = tmp_path / "proj"
    repo.mkdir()
    registry = HarnessVersionRegistry()
    manager = RepositoryManager(harness_versions=registry)

    host = _fake_host(repo, "repo-1")
    bumps: list[Path] = []

    created: list[object] = []

    def _ctor(*_a, **_k):
        created.append(1)
        return host

    monkeypatch.setattr(mgr_mod, "Config", SimpleNamespace(load=lambda _root: object()))
    monkeypatch.setattr(mgr_mod, "RepositoryHost", _ctor)
    monkeypatch.setattr(manager, "_bump_last_seen", lambda root: bumps.append(root))

    first = await manager.activate(repo)
    second = await manager.activate(repo)
    assert first is second is host
    assert len(created) == 1
    assert manager.get("repo-1") is host
    # Reuse path refreshes registry recency; first activate relies on ProcessScope/open_repo_db.
    assert bumps == [repo.resolve()]


async def test_deactivate_stops_host(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    repo = tmp_path / "proj"
    repo.mkdir()
    manager = RepositoryManager()
    host = _fake_host(repo, "repo-1")
    monkeypatch.setattr(mgr_mod, "Config", SimpleNamespace(load=lambda _root: object()))
    monkeypatch.setattr(mgr_mod, "RepositoryHost", lambda *_a, **_k: host)
    monkeypatch.setattr(manager, "_bump_last_seen", lambda _root: None)

    await manager.activate(repo)
    await manager.deactivate("repo-1")
    host.set_plan_seed_failure_notifier.assert_called_once_with(None)
    host.clear_shutdown_signal.assert_called_once()
    host.stop.assert_awaited_once()
    assert manager.get("repo-1") is None


async def test_activate_by_id_reuses_and_resolves(
    monkeypatch: pytest.MonkeyPatch, isolated_db: Path, tmp_path: Path
):
    repo = tmp_path / "by-id"
    repo.mkdir()
    conn = connect(isolated_db)
    try:
        init_db(conn)
        rid = resolve_repository(conn, repo)
        conn.commit()
    finally:
        conn.close()

    manager = RepositoryManager()
    host = _fake_host(repo, rid)
    monkeypatch.setattr(mgr_mod, "Config", SimpleNamespace(load=lambda _root: object()))
    monkeypatch.setattr(mgr_mod, "RepositoryHost", lambda *_a, **_k: host)
    monkeypatch.setattr(manager, "_bump_last_seen", lambda _root: None)

    assert manager.resolve_root(rid) == repo.resolve()
    first = await manager.activate_by_id(rid)
    second = await manager.activate_by_id(rid)
    assert first is second is host

    with pytest.raises(KeyError):
        await manager.activate_by_id("missing-repository-id")


async def test_on_deactivated_hook(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    repo = tmp_path / "hook"
    repo.mkdir()
    manager = RepositoryManager()
    host = _fake_host(repo, "hook-id")
    seen: list[str] = []
    manager.set_on_deactivated(seen.append)
    monkeypatch.setattr(mgr_mod, "Config", SimpleNamespace(load=lambda _root: object()))
    monkeypatch.setattr(mgr_mod, "RepositoryHost", lambda *_a, **_k: host)
    monkeypatch.setattr(manager, "_bump_last_seen", lambda _root: None)

    await manager.activate(repo)
    await manager.deactivate("hook-id")
    assert seen == ["hook-id"]
    host.set_plan_seed_failure_notifier.assert_called_once_with(None)


def test_list_recent_orders_by_last_seen(isolated_db: Path, tmp_path: Path):
    conn = connect(isolated_db)
    try:
        init_db(conn)
        a = tmp_path / "a"
        b = tmp_path / "b"
        a.mkdir()
        b.mkdir()
        id_a = resolve_repository(conn, a)
        id_b = resolve_repository(conn, b)
        # Touch A again so it sorts first.
        resolve_repository(conn, a)
        conn.commit()
    finally:
        conn.close()

    manager = RepositoryManager()
    recent = manager.list_recent()
    assert [r.repository_id for r in recent[:2]] == [id_a, id_b]


def test_initialize_scaffolds(isolated_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = tmp_path / "fresh"
    repo.mkdir()
    scaffolded: list[Path] = []

    def _scaffold(root: Path, *, force: bool = False) -> Path:
        del force
        scaffolded.append(root)
        ad = root / ".murder"
        ad.mkdir(parents=True, exist_ok=True)
        conn = connect(isolated_db)
        try:
            init_db(conn)
            resolve_repository(conn, root)
            conn.commit()
        finally:
            conn.close()
        return ad

    monkeypatch.setattr(mgr_mod, "scaffold_project", _scaffold)
    manager = RepositoryManager()
    entry = manager.initialize(repo)
    assert scaffolded == [repo.resolve()]
    assert entry.root_path.resolve() == repo.resolve()


async def test_idle_eviction_skips_hosts_with_ws_or_agents(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    repo_a = tmp_path / "a"
    repo_b = tmp_path / "b"
    repo_a.mkdir()
    repo_b.mkdir()
    manager = RepositoryManager(idle_timeout_s=0.01)

    host_a = _fake_host(repo_a, "a", live_agents=False)
    host_b = _fake_host(repo_b, "b", live_agents=True)
    hosts = {"a": host_a, "b": host_b}

    def _make_host(cfg, root, **kwargs):
        del cfg, kwargs
        key = "a" if root.name == "a" else "b"
        return hosts[key]

    monkeypatch.setattr(mgr_mod, "Config", SimpleNamespace(load=lambda _root: object()))
    monkeypatch.setattr(mgr_mod, "RepositoryHost", _make_host)
    monkeypatch.setattr(manager, "_bump_last_seen", lambda _root: None)

    await manager.activate(repo_a)
    await manager.activate(repo_b)
    manager.note_ws_connect("a")

    # Force activity timestamps into the past.
    manager._last_activity["a"] = 0.0
    manager._last_activity["b"] = 0.0

    evicted = await manager.evict_idle()
    assert evicted == []
    assert manager.get("a") is host_a
    assert manager.get("b") is host_b

    manager.note_ws_disconnect("a")
    host_b.has_live_agents.return_value = False
    manager._last_activity["a"] = 0.0
    manager._last_activity["b"] = 0.0
    evicted = await manager.evict_idle()
    assert set(evicted) == {"a", "b"}
    assert manager.active == {}


async def test_pinned_host_skips_idle_eviction(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    repo = tmp_path / "pinned"
    repo.mkdir()
    manager = RepositoryManager(idle_timeout_s=0.01)
    host = _fake_host(repo, "pinned-id", live_agents=False)
    monkeypatch.setattr(mgr_mod, "Config", SimpleNamespace(load=lambda _root: object()))
    monkeypatch.setattr(mgr_mod, "RepositoryHost", lambda *_a, **_k: host)
    monkeypatch.setattr(manager, "_bump_last_seen", lambda _root: None)

    await manager.activate(repo)
    manager.pin("pinned-id")
    manager._last_activity["pinned-id"] = 0.0

    assert await manager.evict_idle() == []
    assert manager.get("pinned-id") is host
    assert manager.is_pinned("pinned-id")

    manager.unpin("pinned-id")
    manager._last_activity["pinned-id"] = 0.0
    assert await manager.evict_idle() == ["pinned-id"]


async def test_probe_daemon_listener_requires_live_daemon(monkeypatch: pytest.MonkeyPatch):
    from murder.app.service.daemon_host import probe_daemon_listener

    monkeypatch.setattr(
        "murder.app.service.daemon_host.is_live_daemon", lambda: False
    )
    assert await probe_daemon_listener() is False
