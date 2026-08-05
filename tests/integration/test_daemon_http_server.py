"""Path-scoped DaemonHttpServer: picker API + multi-repo WebSocket routing."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from aiohttp import ClientSession

from murder.app.protocol.common import APPLICATION_PROTOCOL_VERSION
from murder.app.protocol.requests import CommandName, QueryName
from murder.app.service.gateway import ApplicationGateway
from murder.app.service.projection_registry import ProjectionProviderRegistry
from murder.app.service.repository_manager import RecentRepository
from murder.app.service.socket_server import DaemonHttpServer, session_from_host
from murder.facts.log import FactLog, ProjectionInputLog
from murder.state.persistence.connection import RepoDb
from tests.support.database import SECOND_TEST_REPOSITORY_ID, TEST_REPOSITORY_ID, open_test_repo_db


class _Application:
    available_queries = (QueryName.HEALTH_GET,)
    available_commands = ()

    async def query(self, name: QueryName, params: dict[str, object]) -> dict[str, object]:
        del name, params
        return {"ok": True, "pid": 1}

    async def command(self, name: CommandName, params: dict[str, object]) -> dict[str, object]:
        del name, params
        return {}


def _logs(tmp_path: Path, repository_id: str) -> tuple[RepoDb, FactLog, ProjectionInputLog]:
    db = open_test_repo_db(
        tmp_path / f"daemon-http-{repository_id}.db",
        repository_id=repository_id,
    )
    return db, FactLog(db, poll_interval_s=0.01), ProjectionInputLog(db, poll_interval_s=0.01)


@dataclass
class _FakeHost:
    repository_id: str
    run_id: str
    application_dispatcher: Any
    fact_log: FactLog
    projection_input_log: ProjectionInputLog
    projection_providers: ProjectionProviderRegistry
    repo_root: Path
    terminal_capture: Any = None
    terminal_output_open: Any = None
    terminal_input: Any = None
    terminal_input_validator: Any = None
    observability_context: Any = None
    _notifier: Any = None

    def schedule_plan_seed(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    def set_plan_seed_failure_notifier(self, notifier: Any) -> None:
        self._notifier = notifier


@dataclass
class _FakeManager:
    hosts: dict[str, _FakeHost] = field(default_factory=dict)
    roots: dict[str, Path] = field(default_factory=dict)
    recent: list[RecentRepository] = field(default_factory=list)
    ws_counts: dict[str, int] = field(default_factory=dict)
    inits: list[tuple[Path, bool]] = field(default_factory=list)
    activated: list[Path] = field(default_factory=list)
    deactivated: list[str] = field(default_factory=list)
    _on_deactivated: Any = None

    @property
    def active(self) -> dict[str, _FakeHost]:
        return dict(self.hosts)

    def get(self, repository_id: str) -> _FakeHost | None:
        return self.hosts.get(repository_id)

    def get_by_root(self, repo_root: Path) -> _FakeHost | None:
        root = repo_root.resolve(strict=False)
        for host in self.hosts.values():
            if host.repo_root.resolve(strict=False) == root:
                return host
        return None

    def resolve_root(self, repository_id: str) -> Path | None:
        return self.roots.get(repository_id)

    async def activate(self, repo_root: Path) -> _FakeHost:
        root = repo_root.resolve(strict=False)
        self.activated.append(root)
        existing = self.get_by_root(root)
        if existing is not None:
            return existing
        for rid, path in self.roots.items():
            if path.resolve(strict=False) == root:
                host = self.hosts.get(rid)
                if host is not None:
                    return host
                raise KeyError(rid)
        raise FileNotFoundError(f"unknown repository root: {root}")

    async def activate_by_id(self, repository_id: str) -> _FakeHost:
        host = self.hosts.get(repository_id)
        if host is None:
            raise KeyError(repository_id)
        return host

    async def deactivate(self, repository_id: str) -> None:
        self.deactivated.append(repository_id)
        self.hosts.pop(repository_id, None)
        if self._on_deactivated is not None:
            result = self._on_deactivated(repository_id)
            if asyncio.iscoroutine(result) or asyncio.isfuture(result):
                await result
            elif hasattr(result, "__await__"):
                await result

    def note_ws_connect(self, repository_id: str) -> None:
        self.ws_counts[repository_id] = self.ws_counts.get(repository_id, 0) + 1

    def note_ws_disconnect(self, repository_id: str) -> None:
        current = self.ws_counts.get(repository_id, 0)
        if current <= 1:
            self.ws_counts.pop(repository_id, None)
        else:
            self.ws_counts[repository_id] = current - 1

    def list_recent(self) -> list[RecentRepository]:
        return list(self.recent)

    def initialize(self, repo_root: Path, *, force: bool = False) -> RecentRepository:
        self.inits.append((repo_root, force))
        entry = RecentRepository(
            repository_id=str(uuid4()),
            root_path=repo_root.resolve(strict=False),
            created_at="2026-01-01T00:00:00+00:00",
            last_seen_at="2026-01-01T00:00:00+00:00",
        )
        self.recent.insert(0, entry)
        self.roots[entry.repository_id] = entry.root_path
        return entry

    def set_on_deactivated(self, callback: Any) -> None:
        self._on_deactivated = callback


async def _hello(ws: Any, *, client_id: str = "test-client") -> dict[str, Any]:
    await ws.send_json(
        {
            "op": "client.hello",
            "protocol_version": APPLICATION_PROTOCOL_VERSION,
            "client": {"client_id": client_id, "kind": "cli"},
        }
    )
    hello = await ws.receive_json(timeout=2.0)
    assert hello["op"] == "server.hello"
    return hello


@pytest.mark.asyncio
async def test_picker_list_and_init(tmp_path: Path) -> None:
    manager = _FakeManager(
        recent=[
            RecentRepository(
                repository_id="repo-a",
                root_path=tmp_path / "a",
                created_at="t0",
                last_seen_at="t2",
            ),
            RecentRepository(
                repository_id="repo-b",
                root_path=tmp_path / "b",
                created_at="t0",
                last_seen_at="t1",
            ),
        ]
    )
    manager.hosts["repo-a"] = _FakeHost(
        repository_id="repo-a",
        run_id="run-a",
        application_dispatcher=_Application(),
        fact_log=None,  # type: ignore[arg-type]
        projection_input_log=None,  # type: ignore[arg-type]
        projection_providers=ProjectionProviderRegistry(),
        repo_root=tmp_path / "a",
    )
    server = DaemonHttpServer(manager=manager)
    host, port = await server.start(host="127.0.0.1", port=0)
    base = f"http://{host}:{port}"
    try:
        async with ClientSession() as http:
            async with http.get(f"{base}/api/repos") as resp:
                assert resp.status == 200
                payload = await resp.json()
                assert [r["repository_id"] for r in payload["repositories"]] == [
                    "repo-a",
                    "repo-b",
                ]
                assert payload["repositories"][0]["active"] is True
                assert payload["repositories"][1]["active"] is False

            fresh = tmp_path / "fresh"
            fresh.mkdir()
            async with http.post(
                f"{base}/api/repos/init",
                json={"path": str(fresh)},
            ) as resp:
                assert resp.status == 201
                created = await resp.json()
                assert created["root_path"] == str(fresh.resolve())
                assert "repository_id" in created
            assert manager.inits == [(fresh.resolve(), False)]
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_activate_and_deactivate_by_path(tmp_path: Path) -> None:
    db, facts, inputs = _logs(tmp_path, TEST_REPOSITORY_ID)
    root = tmp_path / "proj"
    root.mkdir()
    host = _FakeHost(
        repository_id=TEST_REPOSITORY_ID,
        run_id="run-activate",
        application_dispatcher=_Application(),
        fact_log=facts,
        projection_input_log=inputs,
        projection_providers=ProjectionProviderRegistry(),
        repo_root=root,
    )
    manager = _FakeManager(
        hosts={TEST_REPOSITORY_ID: host},
        roots={TEST_REPOSITORY_ID: root},
        recent=[
            RecentRepository(
                repository_id=TEST_REPOSITORY_ID,
                root_path=root,
                created_at="t0",
                last_seen_at="t1",
            )
        ],
    )
    server = DaemonHttpServer(manager=manager)
    manager.set_on_deactivated(server.close_session)
    try:
        bind_host, port = await server.start(host="127.0.0.1", port=0)
        base = f"http://{bind_host}:{port}"
        async with ClientSession() as http:
            async with http.post(
                f"{base}/api/repos/activate",
                json={"path": str(root)},
            ) as resp:
                assert resp.status == 200
                body = await resp.json()
                assert body["repository_id"] == TEST_REPOSITORY_ID
                assert body["active"] is True
                assert body["websocket_url"] == (
                    f"ws://{bind_host}:{port}/api/ws/{TEST_REPOSITORY_ID}"
                )
            assert manager.activated == [root.resolve()]
            assert server.session_for(TEST_REPOSITORY_ID) is not None

            async with http.post(
                f"{base}/api/repos/deactivate",
                json={"path": str(root)},
            ) as resp:
                assert resp.status == 200
                stopped = await resp.json()
                assert stopped["ok"] is True
                assert stopped["active"] is False
            assert manager.deactivated == [TEST_REPOSITORY_ID]
            assert TEST_REPOSITORY_ID not in manager.hosts
            assert server.session_for(TEST_REPOSITORY_ID) is None
    finally:
        await server.stop()
        db.close()


@pytest.mark.asyncio
async def test_deactivate_closes_connected_websocket(tmp_path: Path) -> None:
    """Deactivation must close live WS clients, not leave them on a stopped host."""
    db, facts, inputs = _logs(tmp_path, TEST_REPOSITORY_ID)
    root = tmp_path / "ws-proj"
    root.mkdir()
    host = _FakeHost(
        repository_id=TEST_REPOSITORY_ID,
        run_id="run-ws-deactivate",
        application_dispatcher=_Application(),
        fact_log=facts,
        projection_input_log=inputs,
        projection_providers=ProjectionProviderRegistry(),
        repo_root=root,
    )
    manager = _FakeManager(
        hosts={TEST_REPOSITORY_ID: host},
        roots={TEST_REPOSITORY_ID: root},
    )
    server = DaemonHttpServer(manager=manager)
    manager.set_on_deactivated(server.close_session)
    try:
        bind_host, port = await server.start(host="127.0.0.1", port=0)
        base = f"http://{bind_host}:{port}"
        ws_url = f"ws://{bind_host}:{port}/api/ws/{TEST_REPOSITORY_ID}"
        async with ClientSession() as http:
            async with http.ws_connect(ws_url) as ws:
                hello = await _hello(ws, client_id="ws-client")
                assert hello["server_id"] == "run-ws-deactivate"
                session = server.session_for(TEST_REPOSITORY_ID)
                assert session is not None
                assert session.connection_count == 1

                async with http.post(
                    f"{base}/api/repos/deactivate",
                    json={"repository_id": TEST_REPOSITORY_ID},
                ) as resp:
                    assert resp.status == 200
                    body = await resp.json()
                    assert body["ok"] is True
                    assert body["active"] is False

                assert manager.deactivated == [TEST_REPOSITORY_ID]
                assert server.session_for(TEST_REPOSITORY_ID) is None
                assert session.closed is True
                assert session.connection_count == 0

                # Client observes the close (aiohttp may surface CLOSED or ERROR).
                msg = await ws.receive(timeout=2.0)
                assert msg.type.name in {"CLOSED", "CLOSE", "ERROR"}
    finally:
        await server.stop()
        db.close()


@pytest.mark.asyncio
async def test_path_scoped_ws_two_repos_distinct_server_ids(tmp_path: Path) -> None:
    db_a, facts_a, inputs_a = _logs(tmp_path, TEST_REPOSITORY_ID)
    db_b, facts_b, inputs_b = _logs(tmp_path, SECOND_TEST_REPOSITORY_ID)
    host_a = _FakeHost(
        repository_id=TEST_REPOSITORY_ID,
        run_id="run-alpha",
        application_dispatcher=_Application(),
        fact_log=facts_a,
        projection_input_log=inputs_a,
        projection_providers=ProjectionProviderRegistry(),
        repo_root=tmp_path / "a",
    )
    host_b = _FakeHost(
        repository_id=SECOND_TEST_REPOSITORY_ID,
        run_id="run-beta",
        application_dispatcher=_Application(),
        fact_log=facts_b,
        projection_input_log=inputs_b,
        projection_providers=ProjectionProviderRegistry(),
        repo_root=tmp_path / "b",
    )
    manager = _FakeManager(
        hosts={
            TEST_REPOSITORY_ID: host_a,
            SECOND_TEST_REPOSITORY_ID: host_b,
        },
        roots={
            TEST_REPOSITORY_ID: tmp_path / "a",
            SECOND_TEST_REPOSITORY_ID: tmp_path / "b",
        },
    )
    server = DaemonHttpServer(manager=manager)
    try:
        host, port = await server.start(host="127.0.0.1", port=0)
        url_a = f"ws://{host}:{port}/api/ws/{TEST_REPOSITORY_ID}"
        url_b = f"ws://{host}:{port}/api/ws/{SECOND_TEST_REPOSITORY_ID}"
        async with ClientSession() as http:
            async with http.ws_connect(url_a) as ws_a, http.ws_connect(url_b) as ws_b:
                hello_a = await _hello(ws_a, client_id="client-a")
                hello_b = await _hello(ws_b, client_id="client-b")
                assert hello_a["server_id"] == "run-alpha"
                assert hello_b["server_id"] == "run-beta"
                await ws_a.send_json(
                    {
                        "op": "request",
                        "request_id": "r-a",
                        "timeout_s": 5,
                        "request": {"kind": "query", "name": "health.get", "params": {}},
                    }
                )
                reply_a = await ws_a.receive_json(timeout=2.0)
                assert reply_a["op"] == "reply"
                assert reply_a["result"]["ok"] is True
        assert manager.ws_counts == {}
    finally:
        await server.stop()
        db_a.close()
        db_b.close()


@pytest.mark.asyncio
async def test_unknown_repository_ws_returns_404() -> None:
    server = DaemonHttpServer(manager=_FakeManager())
    try:
        host, port = await server.start(host="127.0.0.1", port=0)
        async with ClientSession() as http:
            async with http.get(f"http://{host}:{port}/api/ws/missing-id") as resp:
                assert resp.status == 404
                assert "unknown repository_id" in await resp.text()
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_ensure_session_serializes_concurrent_first_connect(
    tmp_path: Path,
) -> None:
    """Two cold activates must share one RepositorySocketSession (no orphan)."""
    db, facts, inputs = _logs(tmp_path, TEST_REPOSITORY_ID)
    host = _FakeHost(
        repository_id=TEST_REPOSITORY_ID,
        run_id="run-1",
        application_dispatcher=_Application(),
        fact_log=facts,
        projection_input_log=inputs,
        projection_providers=ProjectionProviderRegistry(),
        repo_root=tmp_path,
    )
    manager = _FakeManager(
        hosts={TEST_REPOSITORY_ID: host},
        roots={TEST_REPOSITORY_ID: tmp_path},
    )
    server = DaemonHttpServer(manager=manager)
    try:
        first, second = await asyncio.gather(
            server.ensure_session(TEST_REPOSITORY_ID),
            server.ensure_session(TEST_REPOSITORY_ID),
        )
        assert first is second
        assert server.session_for(TEST_REPOSITORY_ID) is first
        assert host._notifier.__func__ is first.notify_plan_seed_failed.__func__
    finally:
        await server.stop()
        db.close()


@pytest.mark.asyncio
async def test_session_from_host_wires_plan_seed_notifier(tmp_path: Path) -> None:
    db, facts, inputs = _logs(tmp_path, TEST_REPOSITORY_ID)
    try:
        host = _FakeHost(
            repository_id=TEST_REPOSITORY_ID,
            run_id="run-1",
            application_dispatcher=_Application(),
            fact_log=facts,
            projection_input_log=inputs,
            projection_providers=ProjectionProviderRegistry(),
            repo_root=tmp_path,
        )
        session = session_from_host(host)
        assert host._notifier is not None
        assert host._notifier.__func__ is session.notify_plan_seed_failed.__func__
        assert session.run_id == "run-1"
        # Gateway wraps the dispatcher.
        assert isinstance(session._gateway, ApplicationGateway)
    finally:
        db.close()


@pytest.mark.asyncio
async def test_static_assets_on_daemon_http(tmp_path: Path) -> None:
    assets = tmp_path / "spa"
    assets.mkdir()
    assets.joinpath("index.html").write_text("<html>daemon-spa</html>", encoding="utf-8")
    manager = _FakeManager()
    server = DaemonHttpServer(manager=manager, assets_dir=assets)
    try:
        host, port = await server.start(host="127.0.0.1", port=0)
        async with ClientSession() as http:
            async with http.get(f"http://{host}:{port}/") as resp:
                assert resp.status == 200
                assert "daemon-spa" in await resp.text()
            async with http.get(f"http://{host}:{port}/missing-route") as resp:
                assert resp.status == 200
                assert "daemon-spa" in await resp.text()
    finally:
        await server.stop()
