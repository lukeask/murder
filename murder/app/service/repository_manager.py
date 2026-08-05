"""RepositoryManager — activate / deactivate / list / init hosts inside one daemon.

Owns at most one ``RepositoryHost`` per ``repository_id``. Exclusivity is
manager-scoped (no per-repo flock). Idle hosts with no WebSocket clients and no
live agents are deactivated after a timeout so a handful of projects stay
memory-sane.

Per-repository lifecycle (``starting`` / ``active`` / ``stopping``) is serialized
with a per-repo lock so concurrent activate/deactivate of the same id cannot
overlap two hosts, while unrelated repositories remain concurrent.
"""

from __future__ import annotations

import asyncio
import contextlib
import enum
import inspect
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from murder.app.service.project_scaffold import scaffold_project
from murder.app.service.repository_host import RepositoryHost
from murder.config import Config, load_repo_env
from murder.llm.harnesses.versioning import HarnessVersionRegistry
from murder.state.persistence.connection import connect, resolve_repository
from murder.state.persistence.schema import init_db
from murder.user_config import UserConfig

LOGGER = logging.getLogger(__name__)

# Keep a few active project stacks; idle ones drop after this quiet period.
DEFAULT_HOST_IDLE_TIMEOUT_S = 300.0
_EVICTION_POLL_S = 30.0


class StaleRepositoryError(LookupError):
    """Registry entry exists but the checkout is missing or uninitialized."""


class RepoLifecyclePhase(enum.Enum):
    STARTING = "starting"
    ACTIVE = "active"
    STOPPING = "stopping"


@dataclass
class _RepoLifecycle:
    """In-flight lifecycle for one ``repository_id`` (or path-keyed interim)."""

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    phase: RepoLifecyclePhase | None = None
    host: RepositoryHost | None = None
    start_future: asyncio.Future[RepositoryHost] | None = None
    stop_future: asyncio.Future[None] | None = None


@dataclass(frozen=True)
class RecentRepository:
    """One row from the shared ``repositories`` registry."""

    repository_id: str
    root_path: Path
    created_at: str
    last_seen_at: str


def require_initialized_checkout(root: Path) -> None:
    """Raise ``FileNotFoundError`` when ``root`` is not an initialized murder project."""
    if not root.is_dir() or not (root / ".murder" / "roles.yaml").is_file():
        raise FileNotFoundError(
            f"repository checkout missing or uninitialized: {root}"
        )


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
        # Pinned hosts skip idle eviction (rare; prefer WS refcounts for normal use).
        self._pinned: set[str] = set()
        self._on_deactivated: (
            Callable[[str], Awaitable[None] | None] | Callable[[str], None] | None
        ) = None
        self._lifecycles: dict[str, _RepoLifecycle] = {}
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

    def resolve_root(self, repository_id: str) -> Path | None:
        """Return the registered ``root_path`` for ``repository_id``, if any."""
        conn = connect()
        try:
            init_db(conn)
            row = conn.execute(
                "SELECT root_path FROM repositories WHERE repository_id = ?",
                (repository_id,),
            ).fetchone()
            if row is None:
                return None
            return Path(str(row["root_path"]))
        finally:
            conn.close()

    def _lookup_id_by_root(self, root: Path) -> str | None:
        """Return a registry ``repository_id`` for ``root`` without inserting a row."""
        resolved = str(root.resolve(strict=False))
        conn = connect()
        try:
            init_db(conn)
            row = conn.execute(
                "SELECT repository_id FROM repositories WHERE root_path = ?",
                (resolved,),
            ).fetchone()
            if row is None:
                return None
            return str(row["repository_id"])
        finally:
            conn.close()

    def _lifecycle(self, key: str) -> _RepoLifecycle:
        life = self._lifecycles.get(key)
        if life is None:
            life = _RepoLifecycle()
            self._lifecycles[key] = life
        return life

    async def _await_stop_if_needed(self, life: _RepoLifecycle) -> None:
        """If ``life`` is stopping, await the shared stop future (lock must be held)."""
        while life.phase is RepoLifecyclePhase.STOPPING:
            fut = life.stop_future
            if fut is None:
                return
            life.lock.release()
            try:
                await fut
            finally:
                await life.lock.acquire()

    async def _await_start_if_needed(
        self, life: _RepoLifecycle
    ) -> RepositoryHost | None:
        """Join an in-flight start; return the host or None if caller should start."""
        if life.phase is not RepoLifecyclePhase.STARTING or life.start_future is None:
            return None
        fut = life.start_future
        life.lock.release()
        try:
            return await fut
        finally:
            await life.lock.acquire()

    async def activate_by_id(self, repository_id: str) -> RepositoryHost:
        """Activate (or reuse) the host for a registered ``repository_id``.

        Raises ``KeyError`` when the id is unknown to the shared registry.
        Raises ``StaleRepositoryError`` when the registry row exists but the
        checkout is missing or no longer initialized.
        """
        existing = self.get(repository_id)
        if existing is not None:
            self.touch(repository_id)
            self._bump_last_seen(existing.repo_root)
            return existing
        root = self.resolve_root(repository_id)
        if root is None:
            raise KeyError(f"unknown repository_id: {repository_id}")
        try:
            require_initialized_checkout(root)
        except FileNotFoundError as exc:
            raise StaleRepositoryError(
                f"stale repository_id={repository_id} root={root}"
            ) from exc
        host = await self._activate_under_id(repository_id, root)
        if host.repository_id != repository_id:
            # Path collision / registry drift — refuse to serve the wrong partition.
            raise RuntimeError(
                f"repository_id mismatch: requested {repository_id}, "
                f"activated {host.repository_id} for {root}"
            )
        return host

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

    def set_on_deactivated(
        self,
        callback: Callable[[str], Awaitable[None] | None]
        | Callable[[str], None]
        | None,
    ) -> None:
        """Optional hook fired during deactivate (close WS session, drop cache)."""
        self._on_deactivated = callback

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

        Requires an initialized checkout (``.murder/roles.yaml``). Unknown
        registry rows are created only after that check succeeds.
        """
        root = Path(repo_root).resolve(strict=False)
        require_initialized_checkout(root)

        existing = self.get_by_root(root)
        if existing is not None:
            rid = existing.repository_id
            self.touch(rid)
            self._bump_last_seen(root)
            return existing

        # Prefer an existing registry id so path and id activation share one lock.
        repository_id = self._lookup_id_by_root(root)
        if repository_id is None:
            # Valid checkout not yet registered — allocate id without starting a host.
            repository_id = self._register_root(root)
        return await self._activate_under_id(repository_id, root)

    def _register_root(self, root: Path) -> str:
        """Insert/refresh the registry row for an already-validated checkout."""
        conn = connect()
        try:
            init_db(conn)
            repository_id = resolve_repository(conn, root)
            conn.commit()
            return repository_id
        finally:
            conn.close()

    async def _activate_under_id(
        self, repository_id: str, root: Path
    ) -> RepositoryHost:
        life = self._lifecycle(repository_id)
        start_future: asyncio.Future[RepositoryHost] | None = None
        while start_future is None:
            async with life.lock:
                await self._await_stop_if_needed(life)

                if life.phase is RepoLifecyclePhase.ACTIVE and life.host is not None:
                    self.touch(repository_id)
                    self._bump_last_seen(root)
                    return life.host
                cached = self._hosts.get(repository_id)
                if cached is not None:
                    self.touch(repository_id)
                    self._bump_last_seen(root)
                    return cached

                joined = await self._await_start_if_needed(life)
                if joined is not None:
                    self.touch(repository_id)
                    self._bump_last_seen(root)
                    return joined

                # Another waiter may have become the starter while we awaited stop.
                if (
                    life.phase is RepoLifecyclePhase.STARTING
                    and life.start_future is not None
                ):
                    continue

                loop = asyncio.get_running_loop()
                start_future = loop.create_future()
                life.phase = RepoLifecyclePhase.STARTING
                life.start_future = start_future

        # Start outside the per-repo lock so concurrent waiters can await
        # ``start_future``; unrelated repos never contended on this lock.
        try:
            cfg = Config.load(root)
            # Parse project env into a per-host mapping; never mutate os.environ.
            repo_env = load_repo_env(root)
            host = RepositoryHost(
                cfg,
                root,
                user_config=self._user_config,
                harness_versions=self._harness_versions,
                repo_env=repo_env,
            )
            await host.start()
            if host.repository_id != repository_id:
                # Registry drift after start — stop the wrong host and fail.
                with contextlib.suppress(Exception):
                    await host.stop()
                raise RuntimeError(
                    f"repository_id mismatch: expected {repository_id}, "
                    f"got {host.repository_id} for {root}"
                )
            async with life.lock:
                # Publish ACTIVE so a concurrent deactivate waiting on start can stop.
                life.host = host
                life.phase = RepoLifecyclePhase.ACTIVE
                life.start_future = None
                self._hosts[repository_id] = host
                self.touch(repository_id)
                if not start_future.done():
                    start_future.set_result(host)
            LOGGER.info(
                "activated repository_id=%s root=%s", repository_id, root
            )
            return host
        except Exception as exc:
            async with life.lock:
                life.host = None
                life.phase = None
                life.start_future = None
                self._hosts.pop(repository_id, None)
                if not start_future.done():
                    start_future.set_exception(exc)
                # Drop empty lifecycle so a later activate starts clean.
                if life.stop_future is None:
                    self._lifecycles.pop(repository_id, None)
            raise

    def _bump_last_seen(self, repo_root: Path) -> None:
        """Refresh ``repositories.last_seen_at`` without starting a new host."""
        conn = connect()
        try:
            init_db(conn)
            resolve_repository(conn, repo_root)
            conn.commit()
        finally:
            conn.close()

    async def _invoke_on_deactivated(self, repository_id: str) -> None:
        callback = self._on_deactivated
        if callback is None:
            return
        with contextlib.suppress(Exception):
            result = callback(repository_id)
            if inspect.isawaitable(result):
                await result

    async def deactivate(self, repository_id: str) -> None:
        """Gracefully stop one host. Tmux sweep remains per-host on ``stop()``.

        Order:
        1. mark ``stopping`` / drop from ``active`` (blocks new connect reuse),
        2. close repository socket session (via ``on_deactivated``),
        3. await connection teardown inside that hook,
        4. stop the host,
        5. clear lifecycle state and complete the shared stop future.
        """
        life = self._lifecycle(repository_id)
        async with life.lock:
            await self._await_stop_if_needed(life)

            # Wait out an in-flight start so we stop the host that finishes it.
            while (
                life.phase is RepoLifecyclePhase.STARTING
                and life.start_future is not None
            ):
                start_fut = life.start_future
                life.lock.release()
                try:
                    with contextlib.suppress(Exception):
                        await start_fut
                finally:
                    await life.lock.acquire()

            host = life.host or self._hosts.get(repository_id)
            if host is None:
                life.phase = None
                life.start_future = None
                life.stop_future = None
                self._lifecycles.pop(repository_id, None)
                return

            loop = asyncio.get_running_loop()
            stop_future: asyncio.Future[None] = loop.create_future()
            life.phase = RepoLifecyclePhase.STOPPING
            life.stop_future = stop_future
            life.host = None
            self._hosts.pop(repository_id, None)
            self._last_activity.pop(repository_id, None)
            self._ws_connections.pop(repository_id, None)
            self._pinned.discard(repository_id)
            LOGGER.info(
                "deactivating repository_id=%s root=%s", repository_id, host.repo_root
            )
            target = host

        # Session close + host stop outside the lock so concurrent activators of
        # *this* repo await ``stop_future`` while unrelated repos proceed.
        try:
            await self._invoke_on_deactivated(repository_id)
            target.set_plan_seed_failure_notifier(None)
            try:
                target.clear_shutdown_signal()
                await target.stop()
            except Exception:
                LOGGER.exception(
                    "error while deactivating repository_id=%s", repository_id
                )
        finally:
            async with life.lock:
                life.phase = None
                life.host = None
                life.stop_future = None
                life.start_future = None
                self._lifecycles.pop(repository_id, None)
                if not stop_future.done():
                    stop_future.set_result(None)

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
    "RepoLifecyclePhase",
    "RepositoryManager",
    "StaleRepositoryError",
    "require_initialized_checkout",
]
