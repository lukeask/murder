"""RepositoryManager — activate / deactivate / list / init hosts inside one daemon.

Owns at most one ``RepositoryHost`` per ``repository_id``. Exclusivity is
manager-scoped (no per-repo flock). Idle hosts with no WebSocket clients and no
live agents are deactivated after a timeout so a handful of projects stay
memory-sane.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from murder.app.service.project_scaffold import scaffold_project
from murder.app.service.repository_host import RepositoryHost
from murder.config import Config
from murder.llm.harnesses.versioning import HarnessVersionRegistry
from murder.state.persistence.connection import connect, resolve_repository
from murder.state.persistence.schema import init_db
from murder.user_config import UserConfig

LOGGER = logging.getLogger(__name__)

# Keep a few active project stacks; idle ones drop after this quiet period.
DEFAULT_HOST_IDLE_TIMEOUT_S = 300.0
_EVICTION_POLL_S = 30.0


@dataclass(frozen=True)
class RecentRepository:
    """One row from the shared ``repositories`` registry."""

    repository_id: str
    root_path: Path
    created_at: str
    last_seen_at: str


class RepositoryManager:
    """Lifecycle owner for in-process ``RepositoryHost`` instances."""

    def __init__(
        self,
        *,
        user_config: UserConfig | None = None,
        harness_versions: HarnessVersionRegistry | None = None,
        idle_timeout_s: float = DEFAULT_HOST_IDLE_TIMEOUT_S,
    ) -> None:
        self._user_config = user_config
        self._harness_versions = harness_versions
        self._idle_timeout_s = idle_timeout_s
        self._hosts: dict[str, RepositoryHost] = {}
        self._last_activity: dict[str, float] = {}
        self._ws_connections: dict[str, int] = {}
        # Pinned hosts skip idle eviction (Phase-2 socket-bound primary until
        # path-scoped sessions track real WS refcounts in Phase 3).
        self._pinned: set[str] = set()
        self._lock = asyncio.Lock()
        self._eviction_task: asyncio.Task[None] | None = None
        self._stopped = asyncio.Event()

    @property
    def active(self) -> dict[str, RepositoryHost]:
        """Snapshot of currently started hosts keyed by repository_id."""
        return dict(self._hosts)

    def get(self, repository_id: str) -> RepositoryHost | None:
        return self._hosts.get(repository_id)

    def get_by_root(self, repo_root: Path) -> RepositoryHost | None:
        root = repo_root.resolve(strict=False)
        for host in self._hosts.values():
            if host.repo_root.resolve(strict=False) == root:
                return host
        return None

    def start_eviction_loop(self) -> None:
        """Begin periodic idle eviction (idempotent)."""
        if self._eviction_task is not None and not self._eviction_task.done():
            return
        self._stopped.clear()
        self._eviction_task = asyncio.create_task(
            self._eviction_loop(), name="repository-manager-eviction"
        )

    async def stop_eviction_loop(self) -> None:
        self._stopped.set()
        task = self._eviction_task
        self._eviction_task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def touch(self, repository_id: str) -> None:
        """Mark a host as recently used (resets idle timer)."""
        if repository_id in self._hosts:
            self._last_activity[repository_id] = time.monotonic()

    def pin(self, repository_id: str) -> None:
        """Exempt ``repository_id`` from idle eviction while it remains active."""
        if repository_id in self._hosts:
            self._pinned.add(repository_id)
            self.touch(repository_id)

    def unpin(self, repository_id: str) -> None:
        """Allow idle eviction for ``repository_id`` again."""
        self._pinned.discard(repository_id)

    def is_pinned(self, repository_id: str) -> bool:
        return repository_id in self._pinned

    def note_ws_connect(self, repository_id: str) -> None:
        """Increment the WebSocket refcount for idle-eviction decisions."""
        self._ws_connections[repository_id] = self._ws_connections.get(repository_id, 0) + 1
        self.touch(repository_id)

    def note_ws_disconnect(self, repository_id: str) -> None:
        """Decrement the WebSocket refcount (floored at zero)."""
        current = self._ws_connections.get(repository_id, 0)
        if current <= 1:
            self._ws_connections.pop(repository_id, None)
        else:
            self._ws_connections[repository_id] = current - 1

    def ws_connection_count(self, repository_id: str) -> int:
        return self._ws_connections.get(repository_id, 0)

    async def activate(self, repo_root: Path) -> RepositoryHost:
        """Open (or reuse) a started ``RepositoryHost`` for ``repo_root``.

        ``ProcessScope.open`` → ``open_repo_db`` bumps ``last_seen_at``.
        """
        root = Path(repo_root).resolve(strict=False)
        async with self._lock:
            existing = self.get_by_root(root)
            if existing is not None:
                rid = existing.repository_id
                self.touch(rid)
                self._bump_last_seen(root)
                return existing

            cfg = Config.load(root)
            host = RepositoryHost(
                cfg,
                root,
                user_config=self._user_config,
                harness_versions=self._harness_versions,
            )
            await host.start()
            repository_id = host.repository_id
            self._hosts[repository_id] = host
            self.touch(repository_id)
            LOGGER.info(
                "activated repository_id=%s root=%s", repository_id, root
            )
            return host

    def _bump_last_seen(self, repo_root: Path) -> None:
        """Refresh ``repositories.last_seen_at`` without starting a new host."""
        conn = connect()
        try:
            init_db(conn)
            resolve_repository(conn, repo_root)
            conn.commit()
        finally:
            conn.close()

    async def deactivate(self, repository_id: str) -> None:
        """Gracefully stop one host. Tmux sweep remains per-host on ``stop()``."""
        async with self._lock:
            host = self._hosts.pop(repository_id, None)
            self._last_activity.pop(repository_id, None)
            self._ws_connections.pop(repository_id, None)
            self._pinned.discard(repository_id)
            if host is None:
                return
            LOGGER.info("deactivating repository_id=%s root=%s", repository_id, host.repo_root)
            target = host
        # Stop outside the lock so concurrent activate of another repo can proceed.
        try:
            target.clear_shutdown_signal()
            await target.stop()
        except Exception:
            LOGGER.exception(
                "error while deactivating repository_id=%s", repository_id
            )

    async def deactivate_all(self) -> None:
        """Stop every active host (daemon shutdown)."""
        await self.stop_eviction_loop()
        for repository_id in list(self._hosts):
            await self.deactivate(repository_id)

    def list_recent(self) -> list[RecentRepository]:
        """Return registry rows ordered by ``last_seen_at`` descending."""
        conn = connect()
        try:
            init_db(conn)
            rows = conn.execute(
                "SELECT repository_id, root_path, created_at, last_seen_at "
                "FROM repositories ORDER BY last_seen_at DESC"
            ).fetchall()
            return [
                RecentRepository(
                    repository_id=str(row["repository_id"]),
                    root_path=Path(str(row["root_path"])),
                    created_at=str(row["created_at"]),
                    last_seen_at=str(row["last_seen_at"]),
                )
                for row in rows
            ]
        finally:
            conn.close()

    def initialize(self, repo_root: Path, *, force: bool = False) -> RecentRepository:
        """Scaffold a project and return its registry row (does not start a host)."""
        root = Path(repo_root).resolve(strict=False)
        scaffold_project(root, force=force)
        # scaffold_project already opened the partition (bumps last_seen_at).
        for entry in self.list_recent():
            if entry.root_path.resolve(strict=False) == root:
                return entry
        raise RuntimeError(f"scaffold completed but repository row missing for {root}")

    def is_idle(self, repository_id: str) -> bool:
        """True when the host has no WS clients and no non-terminal agents."""
        host = self._hosts.get(repository_id)
        if host is None:
            return True
        if repository_id in self._pinned:
            return False
        if self.ws_connection_count(repository_id) > 0:
            return False
        return not host.has_live_agents()

    async def evict_idle(self) -> list[str]:
        """Deactivate hosts idle longer than ``idle_timeout_s``. Returns ids stopped."""
        now = time.monotonic()
        victims: list[str] = []
        for repository_id in list(self._hosts):
            if not self.is_idle(repository_id):
                self.touch(repository_id)
                continue
            last = self._last_activity.get(repository_id, now)
            if now - last >= self._idle_timeout_s:
                victims.append(repository_id)
        for repository_id in victims:
            await self.deactivate(repository_id)
        return victims

    async def _eviction_loop(self) -> None:
        while not self._stopped.is_set():
            try:
                await asyncio.wait_for(
                    self._stopped.wait(),
                    timeout=min(_EVICTION_POLL_S, max(1.0, self._idle_timeout_s / 2)),
                )
                return
            except TimeoutError:
                pass
            try:
                evicted = await self.evict_idle()
                if evicted:
                    LOGGER.info("idle-evicted repositories: %s", ", ".join(evicted))
            except Exception:
                LOGGER.exception("idle eviction pass failed")


__all__ = [
    "DEFAULT_HOST_IDLE_TIMEOUT_S",
    "RecentRepository",
    "RepositoryManager",
]
