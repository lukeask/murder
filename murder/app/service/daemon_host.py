"""DaemonHost — process-wide murder daemon (flock, port, signals, multi-host).

One process owns ``config_dir()/daemon.lock``, a single aiohttp listener on
``127.0.0.1:62077``, user themes/config, and daemon-scoped workers
(``ModelCatalogRefreshWorker``, ``HarnessVersionProbeWorker``). Per-repo work
lives in ``RepositoryManager`` / ``RepositoryHost``.

Path-scoped routing: ``DaemonHttpServer`` serves the SPA, picker HTTP API, and
``GET /api/ws/{repository_id}`` → per-repo ``RepositorySocketSession``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
from pathlib import Path

from murder.app.protocol.common import DAEMON_WEBSOCKET_HOST, DAEMON_WEBSOCKET_PORT
from murder.app.service.repository_host import RepositoryHost
from murder.app.service.repository_manager import (
    DEFAULT_HOST_IDLE_TIMEOUT_S,
    RepositoryManager,
)
from murder.app.service.socket_server import DaemonHttpServer
from murder.app.service.supervisor import Supervisor
from murder.app.service.webui_assets import resolve_webui_assets_dir
from murder.config import Config
from murder.llm.harnesses.versioning import HarnessVersionRegistry
from murder.runtime.workers import HarnessVersionProbeWorker, ModelCatalogRefreshWorker
from murder.runtime.workers.base import WorkerCtx
from murder.state.storage.filesystem import (
    acquire_flock,
    lock_is_held,
    read_lock_pid,
    release_flock,
)
from murder.state.storage.service_registry import (
    remove_daemon_record,
    write_daemon_record,
)
from murder.user_config import (
    UserConfig,
    config_dir,
    ensure_user_themes,
    load_user_config,
)

LOGGER = logging.getLogger(__name__)


def daemon_lock_path() -> Path:
    """Exclusive daemon flock path under the user config directory."""
    return config_dir() / "daemon.lock"


def live_daemon_pid() -> int | None:
    """Return the pid of a live daemon holding ``daemon.lock``, if any."""
    path = daemon_lock_path()
    pid = read_lock_pid(path)
    if pid is None:
        return None
    try:
        os.kill(pid, 0)
    except OSError:
        return None
    if not lock_is_held(path):
        return None
    return pid


def is_live_daemon() -> bool:
    """True when another process holds the daemon flock and its pid is alive."""
    return live_daemon_pid() is not None


async def probe_daemon_listener(
    *,
    bind_host: str = DAEMON_WEBSOCKET_HOST,
    port: int = DAEMON_WEBSOCKET_PORT,
    timeout_s: float = 0.5,
) -> bool:
    """Return whether a live murder daemon is accepting TCP on ``bind_host:port``.

    CLI ensure-startup (Phase 4) uses this to attach instead of spawning a second
    ``serviced``. Requires ``is_live_daemon()`` so a random process on the port
    is not treated as murder.
    """
    if not is_live_daemon():
        return False
    try:
        _reader, writer = await asyncio.wait_for(
            asyncio.open_connection(bind_host, port),
            timeout=timeout_s,
        )
    except (OSError, asyncio.TimeoutError):
        return False
    writer.close()
    with contextlib.suppress(Exception):
        await writer.wait_closed()
    return True


class DaemonHost:
    """Process composition root for the single-daemon multi-repo service."""

    def __init__(
        self,
        *,
        idle_timeout_s: float = DEFAULT_HOST_IDLE_TIMEOUT_S,
    ) -> None:
        self._idle_timeout_s = idle_timeout_s
        self._lock_fd: int | None = None
        self._user_config: UserConfig | None = None
        self.harness_versions = HarnessVersionRegistry()
        self.manager: RepositoryManager | None = None
        self._daemon_supervisor: Supervisor | None = None
        self._http: DaemonHttpServer | None = None
        self._primary_repository_id: str | None = None
        self._stop = asyncio.Event()
        self.bound: tuple[str, int] | None = None

    async def run(
        self,
        *,
        initial_repo: Path,
        bind_host: str = DAEMON_WEBSOCKET_HOST,
        port: int = DAEMON_WEBSOCKET_PORT,
    ) -> None:
        """Acquire exclusivity, activate ``initial_repo``, serve, wait for signal."""
        ensure_user_themes()
        try:
            self._user_config = load_user_config()
        except Exception:
            LOGGER.debug("user config unavailable; continuing with defaults", exc_info=True)
            self._user_config = None

        self._lock_fd = acquire_flock(daemon_lock_path())
        self.manager = RepositoryManager(
            user_config=self._user_config,
            harness_versions=self.harness_versions,
            idle_timeout_s=self._idle_timeout_s,
        )
        try:
            await self._start_daemon_workers(probe_config=Config.load(initial_repo))
            self.manager.start_eviction_loop()
            primary = await self.manager.activate(initial_repo)
            await self._attach_http(primary, bind_host=bind_host, port=port)
            try:
                await self._wait_for_signal()
            finally:
                await self._ordered_shutdown(authoritative=True)
        finally:
            await self._ordered_shutdown(authoritative=False)
            if self._lock_fd is not None:
                with contextlib.suppress(Exception):
                    release_flock(self._lock_fd)
                self._lock_fd = None
            with contextlib.suppress(FileNotFoundError, OSError):
                daemon_lock_path().unlink()

    async def _start_daemon_workers(self, *, probe_config: Config) -> None:
        """Start the two user-global workers once for the whole process."""
        ctx = WorkerCtx(
            repo_root=config_dir(),
            run_id="daemon",
        )
        supervisor = Supervisor(ctx)
        try:
            await supervisor.start_worker(ModelCatalogRefreshWorker())
            await supervisor.start_worker(
                HarnessVersionProbeWorker(
                    updater=self.harness_versions.replace,
                    config=probe_config,
                )
            )
        except BaseException:
            with contextlib.suppress(Exception):
                await supervisor.stop_all()
            raise
        self._daemon_supervisor = supervisor

    async def _attach_http(
        self,
        primary: RepositoryHost,
        *,
        bind_host: str,
        port: int,
    ) -> None:
        """Bind ``DaemonHttpServer`` and publish the daemon registry record."""
        assert self.manager is not None
        http = DaemonHttpServer(
            manager=self.manager,
            assets_dir=resolve_webui_assets_dir(primary.repo_root),
        )
        self.manager.set_on_deactivated(http.drop_session)
        # Warm the primary session so plan-seed notifier is wired before clients.
        await http.ensure_session(primary.repository_id)
        bound = await http.start(host=bind_host, port=port)
        # Assign before registry write so ordered shutdown can stop the listener
        # if publication fails mid-attach.
        self._http = http
        self.bound = bound
        self._primary_repository_id = primary.repository_id
        write_daemon_record(port=bound[1])
        LOGGER.info(
            "daemon listening on http://%s:%d/ (ws path /api/ws/{repository_id}; primary=%s)",
            bound[0],
            bound[1],
            primary.repository_id,
        )

    async def _wait_for_signal(self) -> None:
        loop = asyncio.get_running_loop()

        def _wake() -> None:
            self._stop.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, _wake)
        await self._stop.wait()

    async def _ordered_shutdown(self, *, authoritative: bool) -> None:
        """Mirror former ServiceHost / Phase-2 bridge stop ordering."""
        manager = self.manager
        if manager is not None and authoritative and self._primary_repository_id is not None:
            primary = manager.get(self._primary_repository_id)
            if primary is not None:
                with contextlib.suppress(Exception):
                    primary.clear_shutdown_signal()

        if self._http is not None:
            with contextlib.suppress(FileNotFoundError, OSError, Exception):
                await self._http.stop()
            self._http = None
            self.bound = None

        with contextlib.suppress(Exception):
            remove_daemon_record()

        self._primary_repository_id = None

        if manager is not None:
            manager.set_on_deactivated(None)
            with contextlib.suppress(Exception):
                await manager.deactivate_all()
            self.manager = None

        if self._daemon_supervisor is not None:
            with contextlib.suppress(Exception):
                await self._daemon_supervisor.stop_all()
            self._daemon_supervisor = None


__all__ = [
    "DaemonHost",
    "daemon_lock_path",
    "is_live_daemon",
    "live_daemon_pid",
    "probe_daemon_listener",
]
