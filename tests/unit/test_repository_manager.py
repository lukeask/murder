"""Unit tests for RepositoryManager activate / deactivate / list / idle eviction."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from murder.app.service import repository_manager as mgr_mod
from murder.app.service.repository_manager import RepositoryManager, StaleRepositoryError
from murder.llm.harnesses.versioning import HarnessVersionRegistry
from murder.state.persistence.connection import connect, resolve_repository
from murder.state.persistence.schema import init_db


def _init_checkout(repo: Path) -> Path:
    """Create a minimal initialized murder checkout (roles.yaml present)."""
    repo.mkdir(parents=True, exist_ok=True)
    murder = repo / ".murder"
    murder.mkdir(exist_ok=True)
    roles = murder / "roles.yaml"
    if not roles.is_file():
        roles.write_text("project:\n  name: test\n", encoding="utf-8")
    return repo


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
    repo = _init_checkout(tmp_path / "proj")
    registry = HarnessVersionRegistry()
    manager = RepositoryManager(harness_versions=registry)

    host = _fake_host(repo, "repo-1")
    bumps: list[Path] = []

    created: list[object] = []

    def _ctor(*_a, **_k):
        created.append(1)
        return host

    monkeypatch.setattr(mgr_mod, "Config", SimpleNamespace(load=lambda _root: object()))
    monkeypatch.setattr(mgr_mod, "load_repo_env", lambda _root: {})
    monkeypatch.setattr(mgr_mod, "RepositoryHost", _ctor)
    monkeypatch.setattr(manager, "_bump_last_seen", lambda root: bumps.append(root))
    monkeypatch.setattr(manager, "_lookup_id_by_root", lambda _root: "repo-1")
    monkeypatch.setattr(manager, "_register_root", lambda _root: "repo-1")

    first = await manager.activate(repo)
    second = await manager.activate(repo)
    assert first is second is host
    assert len(created) == 1
    assert manager.get("repo-1") is host
    # Reuse path refreshes registry recency; first activate relies on ProcessScope/open_repo_db.
    assert bumps == [repo.resolve()]


async def test_deactivate_stops_host(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    repo = _init_checkout(tmp_path / "proj")
    manager = RepositoryManager()
    host = _fake_host(repo, "repo-1")
    monkeypatch.setattr(mgr_mod, "Config", SimpleNamespace(load=lambda _root: object()))
    monkeypatch.setattr(mgr_mod, "load_repo_env", lambda _root: {})
    monkeypatch.setattr(mgr_mod, "RepositoryHost", lambda *_a, **_k: host)
    monkeypatch.setattr(manager, "_bump_last_seen", lambda _root: None)
    monkeypatch.setattr(manager, "_lookup_id_by_root", lambda _root: "repo-1")
    monkeypatch.setattr(manager, "_register_root", lambda _root: "repo-1")

    await manager.activate(repo)
    await manager.deactivate("repo-1")
    host.set_plan_seed_failure_notifier.assert_called_once_with(None)
    host.clear_shutdown_signal.assert_called_once()
    host.stop.assert_awaited_once()
    assert manager.get("repo-1") is None


async def test_activate_by_id_reuses_and_resolves(
    monkeypatch: pytest.MonkeyPatch, isolated_db: Path, tmp_path: Path
):
    repo = _init_checkout(tmp_path / "by-id")
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
    monkeypatch.setattr(mgr_mod, "load_repo_env", lambda _root: {})
    monkeypatch.setattr(mgr_mod, "RepositoryHost", lambda *_a, **_k: host)
    monkeypatch.setattr(manager, "_bump_last_seen", lambda _root: None)

    assert manager.resolve_root(rid) == repo.resolve()
    first = await manager.activate_by_id(rid)
    second = await manager.activate_by_id(rid)
    assert first is second is host

    with pytest.raises(KeyError):
        await manager.activate_by_id("missing-repository-id")


async def test_activate_by_id_rejects_stale_checkout(
    monkeypatch: pytest.MonkeyPatch, isolated_db: Path, tmp_path: Path
):
    repo = tmp_path / "deleted-proj"
    repo.mkdir()
    conn = connect(isolated_db)
    try:
        init_db(conn)
        rid = resolve_repository(conn, repo)
        conn.commit()
    finally:
        conn.close()
    # Simulate deleted checkout after registry entry exists.
    for child in list(repo.iterdir()):
        if child.is_dir():
            for nested in sorted(child.rglob("*"), reverse=True):
                if nested.is_file():
                    nested.unlink()
                elif nested.is_dir():
                    nested.rmdir()
            child.rmdir()
        else:
            child.unlink()
    repo.rmdir()

    manager = RepositoryManager()
    with pytest.raises(StaleRepositoryError, match="stale repository_id"):
        await manager.activate_by_id(rid)
    assert manager.get(rid) is None


async def test_activate_rejects_missing_roles_yaml(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    repo = tmp_path / "bare"
    repo.mkdir()
    manager = RepositoryManager()
    with pytest.raises(FileNotFoundError, match="uninitialized"):
        await manager.activate(repo)


async def test_on_deactivated_hook(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    repo = _init_checkout(tmp_path / "hook")
    manager = RepositoryManager()
    host = _fake_host(repo, "hook-id")
    seen: list[str] = []
    manager.set_on_deactivated(seen.append)
    monkeypatch.setattr(mgr_mod, "Config", SimpleNamespace(load=lambda _root: object()))
    monkeypatch.setattr(mgr_mod, "load_repo_env", lambda _root: {})
    monkeypatch.setattr(mgr_mod, "RepositoryHost", lambda *_a, **_k: host)
    monkeypatch.setattr(manager, "_bump_last_seen", lambda _root: None)
    monkeypatch.setattr(manager, "_lookup_id_by_root", lambda _root: "hook-id")
    monkeypatch.setattr(manager, "_register_root", lambda _root: "hook-id")

    await manager.activate(repo)
    await manager.deactivate("hook-id")
    assert seen == ["hook-id"]
    host.set_plan_seed_failure_notifier.assert_called_once_with(None)


async def test_activate_waits_for_in_flight_stop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Concurrent reactivate must not start a second host until stop finishes."""
    repo = _init_checkout(tmp_path / "race")
    manager = RepositoryManager()
    stop_gate = asyncio.Event()
    stop_entered = asyncio.Event()
    hosts: list[MagicMock] = []

    def _make_host(*_a, **_k):
        host = _fake_host(repo, "race-id")

        async def _stop() -> None:
            stop_entered.set()
            await stop_gate.wait()

        host.stop = AsyncMock(side_effect=_stop)
        hosts.append(host)
        return host

    monkeypatch.setattr(mgr_mod, "Config", SimpleNamespace(load=lambda _root: object()))
    monkeypatch.setattr(mgr_mod, "load_repo_env", lambda _root: {})
    monkeypatch.setattr(mgr_mod, "RepositoryHost", _make_host)
    monkeypatch.setattr(manager, "_bump_last_seen", lambda _root: None)
    monkeypatch.setattr(manager, "_lookup_id_by_root", lambda _root: "race-id")
    monkeypatch.setattr(manager, "_register_root", lambda _root: "race-id")

    first = await manager.activate(repo)
    assert first is hosts[0]

    deactivate_task = asyncio.create_task(manager.deactivate("race-id"))
    await stop_entered.wait()
    # Host removed from active while stop still runs.
    assert manager.get("race-id") is None

    activate_task = asyncio.create_task(manager.activate(repo))
    await asyncio.sleep(0.05)
    assert not activate_task.done()
    assert len(hosts) == 1

    stop_gate.set()
    await deactivate_task
    second = await activate_task
    assert second is hosts[1]
    assert second is not first
    hosts[0].stop.assert_awaited_once()


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
    repo_a = _init_checkout(tmp_path / "a")
    repo_b = _init_checkout(tmp_path / "b")
    manager = RepositoryManager(idle_timeout_s=0.01)

    host_a = _fake_host(repo_a, "a", live_agents=False)
    host_b = _fake_host(repo_b, "b", live_agents=True)
    hosts = {"a": host_a, "b": host_b}

    def _make_host(cfg, root, **kwargs):
        del cfg, kwargs
        key = "a" if root.name == "a" else "b"
        return hosts[key]

    monkeypatch.setattr(mgr_mod, "Config", SimpleNamespace(load=lambda _root: object()))
    monkeypatch.setattr(mgr_mod, "load_repo_env", lambda _root: {})
    monkeypatch.setattr(mgr_mod, "RepositoryHost", _make_host)
    monkeypatch.setattr(manager, "_bump_last_seen", lambda _root: None)
    monkeypatch.setattr(
        manager,
        "_lookup_id_by_root",
        lambda root: "a" if root.name == "a" else "b",
    )
    monkeypatch.setattr(
        manager,
        "_register_root",
        lambda root: "a" if root.name == "a" else "b",
    )

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
    repo = _init_checkout(tmp_path / "pinned")
    manager = RepositoryManager(idle_timeout_s=0.01)
    host = _fake_host(repo, "pinned-id", live_agents=False)
    monkeypatch.setattr(mgr_mod, "Config", SimpleNamespace(load=lambda _root: object()))
    monkeypatch.setattr(mgr_mod, "load_repo_env", lambda _root: {})
    monkeypatch.setattr(mgr_mod, "RepositoryHost", lambda *_a, **_k: host)
    monkeypatch.setattr(manager, "_bump_last_seen", lambda _root: None)
    monkeypatch.setattr(manager, "_lookup_id_by_root", lambda _root: "pinned-id")
    monkeypatch.setattr(manager, "_register_root", lambda _root: "pinned-id")

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
