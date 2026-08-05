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
    import shutil

    repo = tmp_path / "deleted-proj"
    repo.mkdir()
    conn = connect(isolated_db)
    try:
        init_db(conn)
        rid = resolve_repository(conn, repo)
        conn.commit()
    finally:
        conn.close()
    shutil.rmtree(repo)

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


async def test_start_waiter_retries_when_host_stops_before_join_returns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """A start_future waiter must not return a host that was deactivated mid-await."""
    repo = _init_checkout(tmp_path / "join-stop")
    manager = RepositoryManager()
    start_gate = asyncio.Event()
    start_entered = asyncio.Event()
    resume_waiter = asyncio.Event()
    hosts: list[MagicMock] = []
    generation = 0

    def _make_host(*_a, **_k):
        nonlocal generation
        generation += 1
        host = _fake_host(repo, "join-id")
        gen = generation

        async def _start() -> None:
            if gen == 1:
                start_entered.set()
                await start_gate.wait()

        host.start = AsyncMock(side_effect=_start)
        hosts.append(host)
        return host

    async def _await_start_gated(life: object) -> object | None:
        from murder.app.service.repository_manager import RepoLifecyclePhase

        assert isinstance(life, mgr_mod._RepoLifecycle)
        if life.phase is not RepoLifecyclePhase.STARTING or life.start_future is None:
            return None
        fut = life.start_future
        life.lock.release()
        try:
            host = await fut
        finally:
            # Park after start resolves so deactivate can finish before re-validate.
            await resume_waiter.wait()
            await life.lock.acquire()
        return host

    monkeypatch.setattr(mgr_mod, "Config", SimpleNamespace(load=lambda _root: object()))
    monkeypatch.setattr(mgr_mod, "load_repo_env", lambda _root: {})
    monkeypatch.setattr(mgr_mod, "RepositoryHost", _make_host)
    monkeypatch.setattr(manager, "_bump_last_seen", lambda _root: None)
    monkeypatch.setattr(manager, "_lookup_id_by_root", lambda _root: "join-id")
    monkeypatch.setattr(manager, "_register_root", lambda _root: "join-id")
    monkeypatch.setattr(manager, "_await_start_if_needed", _await_start_gated)

    starter = asyncio.create_task(manager.activate(repo))
    await start_entered.wait()
    waiter = asyncio.create_task(manager.activate(repo))
    await asyncio.sleep(0.05)
    assert not starter.done()
    assert not waiter.done()
    assert len(hosts) == 1

    start_gate.set()
    first = await starter
    assert first is hosts[0]
    await manager.deactivate("join-id")
    assert manager.get("join-id") is None
    resume_waiter.set()
    second = await waiter
    assert second is hosts[1]
    assert second is not first
    assert manager.get("join-id") is hosts[1]


async def test_two_reactivators_share_one_lifecycle_after_stop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """A waiter on stop plus a post-stop activator must not split into two hosts.

    Exercises the orphaned-lifecycle race: deactivate used to pop the lifecycle
    before resolving ``stop_future``, so the waiting activator resumed on a
    discarded object while a new activator created a second lifecycle.
    """
    repo = _init_checkout(tmp_path / "split")
    manager = RepositoryManager()
    stop_gate = asyncio.Event()
    stop_entered = asyncio.Event()
    restart_start_gate = asyncio.Event()
    restart_start_entered = asyncio.Event()
    hosts: list[MagicMock] = []
    generation = 0

    def _make_host(*_a, **_k):
        nonlocal generation
        generation += 1
        host = _fake_host(repo, "split-id")
        gen = generation

        async def _stop() -> None:
            stop_entered.set()
            await stop_gate.wait()

        async def _start() -> None:
            if gen == 1:
                return
            restart_start_entered.set()
            await restart_start_gate.wait()

        host.stop = AsyncMock(side_effect=_stop)
        host.start = AsyncMock(side_effect=_start)
        hosts.append(host)
        return host

    monkeypatch.setattr(mgr_mod, "Config", SimpleNamespace(load=lambda _root: object()))
    monkeypatch.setattr(mgr_mod, "load_repo_env", lambda _root: {})
    monkeypatch.setattr(mgr_mod, "RepositoryHost", _make_host)
    monkeypatch.setattr(manager, "_bump_last_seen", lambda _root: None)
    monkeypatch.setattr(manager, "_lookup_id_by_root", lambda _root: "split-id")
    monkeypatch.setattr(manager, "_register_root", lambda _root: "split-id")

    first = await manager.activate(repo)
    assert first is hosts[0]
    assert len(hosts) == 1

    deactivate_task = asyncio.create_task(manager.deactivate("split-id"))
    await stop_entered.wait()
    assert manager.get("split-id") is None

    # Activator already waiting on stop_future while teardown runs.
    waiting_activate = asyncio.create_task(manager.activate(repo))
    await asyncio.sleep(0.05)
    assert not waiting_activate.done()
    assert len(hosts) == 1

    stop_gate.set()
    await deactivate_task

    # Second activator arrives immediately after stop completion — must join the
    # same lifecycle as ``waiting_activate``, not construct a parallel host.
    racing_activate = asyncio.create_task(manager.activate(repo))
    await restart_start_entered.wait()
    await asyncio.sleep(0.05)
    assert len(hosts) == 2

    restart_start_gate.set()
    second = await waiting_activate
    third = await racing_activate
    assert second is third is hosts[1]
    assert manager.get("split-id") is hosts[1]


async def test_initialize_force_stops_active_host(
    monkeypatch: pytest.MonkeyPatch, isolated_db: Path, tmp_path: Path
):
    """Forced init tears down a live host before scaffolding; non-force refuses."""
    from murder.app.service.project_scaffold import ProjectAlreadyInitialized

    repo = _init_checkout(tmp_path / "force-init")
    manager = RepositoryManager()
    host = _fake_host(repo, "force-id")
    stop_gate = asyncio.Event()
    stop_entered = asyncio.Event()

    async def _stop() -> None:
        stop_entered.set()
        await stop_gate.wait()

    host.stop = AsyncMock(side_effect=_stop)
    monkeypatch.setattr(mgr_mod, "Config", SimpleNamespace(load=lambda _root: object()))
    monkeypatch.setattr(mgr_mod, "load_repo_env", lambda _root: {})
    monkeypatch.setattr(mgr_mod, "RepositoryHost", lambda *_a, **_k: host)
    monkeypatch.setattr(manager, "_bump_last_seen", lambda _root: None)
    monkeypatch.setattr(manager, "_lookup_id_by_root", lambda _root: "force-id")
    monkeypatch.setattr(manager, "_register_root", lambda _root: "force-id")

    await manager.activate(repo)
    assert manager.get("force-id") is host

    with pytest.raises(ProjectAlreadyInitialized):
        await manager.initialize(repo, force=False)

    scaffolded: list[tuple[Path, bool]] = []

    def _scaffold(root: Path, *, force: bool = False) -> Path:
        assert stop_gate.is_set(), "scaffold must wait for host stop"
        assert manager.get("force-id") is None
        scaffolded.append((root, force))
        ad = root / ".murder"
        ad.mkdir(parents=True, exist_ok=True)
        (ad / "roles.yaml").write_text("project:\n  name: reset\n", encoding="utf-8")
        conn = connect(isolated_db)
        try:
            init_db(conn)
            resolve_repository(conn, root)
            conn.commit()
        finally:
            conn.close()
        return ad

    monkeypatch.setattr(mgr_mod, "scaffold_project", _scaffold)

    init_task = asyncio.create_task(manager.initialize(repo, force=True))
    await stop_entered.wait()
    assert manager.get("force-id") is None
    assert scaffolded == []

    # Concurrent activate must wait out force-init stop+scaffold.
    activate_task = asyncio.create_task(manager.activate(repo))
    await asyncio.sleep(0.05)
    assert not activate_task.done()

    stop_gate.set()
    entry = await init_task
    assert scaffolded == [(repo.resolve(), True)]
    assert entry.root_path.resolve() == repo.resolve()
    await activate_task
    host.stop.assert_awaited_once()


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


async def test_initialize_scaffolds(
    isolated_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
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
    entry = await manager.initialize(repo)
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


async def test_eviction_yields_to_concurrent_ws_connect(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """A connect that lands after victim selection must keep the host alive."""
    repo = _init_checkout(tmp_path / "race-evict")
    manager = RepositoryManager(idle_timeout_s=0.01)
    host = _fake_host(repo, "race-id", live_agents=False)
    monkeypatch.setattr(mgr_mod, "Config", SimpleNamespace(load=lambda _root: object()))
    monkeypatch.setattr(mgr_mod, "load_repo_env", lambda _root: {})
    monkeypatch.setattr(mgr_mod, "RepositoryHost", lambda *_a, **_k: host)
    monkeypatch.setattr(manager, "_bump_last_seen", lambda _root: None)
    monkeypatch.setattr(manager, "_lookup_id_by_root", lambda _root: "race-id")
    monkeypatch.setattr(manager, "_register_root", lambda _root: "race-id")

    await manager.activate(repo)

    # Live WS refcount → never selected.
    manager.note_ws_connect("race-id")
    manager._last_activity["race-id"] = 0.0
    assert await manager.evict_idle() == []
    assert manager.get("race-id") is host

    # TOCTOU: connect lands after victim selection but before the re-check.
    # Inject at the _evict_victim seam so the test does not depend on how many
    # idleness probes each phase makes.
    manager.note_ws_disconnect("race-id")
    manager._last_activity["race-id"] = 0.0
    real_evict_victim = manager._evict_victim

    async def _connect_then_evict(repository_id: str) -> bool:
        manager.note_ws_connect(repository_id)
        return await real_evict_victim(repository_id)

    monkeypatch.setattr(manager, "_evict_victim", _connect_then_evict)
    assert await manager.evict_idle() == []
    assert manager.get("race-id") is host
    assert manager.ws_connection_count("race-id") == 1
    host.stop.assert_not_awaited()


async def test_probe_daemon_listener_requires_live_daemon(monkeypatch: pytest.MonkeyPatch):
    from murder.app.service.daemon_host import probe_daemon_listener

    monkeypatch.setattr(
        "murder.app.service.daemon_host.is_live_daemon", lambda: False
    )
    assert await probe_daemon_listener() is False
