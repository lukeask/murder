"""Lifecycle smoke: init → supervisor → connect → snapshot → down."""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from murder.bus.protocol import ClientKind
from murder.cli.init_cmd import _scaffold_project
from murder.config import Config
from murder.storage.service_registry import project_session_name
from murder.tui.client import TuiRuntimeClient

_STARTUP_TIMEOUT_S = 15.0
_CONNECT_RETRY_S = 0.25
_SHUTDOWN_TIMEOUT_S = 10


async def _connect_with_retry(
    repo: Path,
    socket_path: Path,
    cfg: Config,
    *,
    deadline: float,
) -> TuiRuntimeClient:
    """Retry health.ping until the supervisor accepts connections or deadline passes."""
    last_exc: Exception | None = None
    while time.monotonic() < deadline:
        try:
            client = TuiRuntimeClient(
                repo, socket_path, cfg, client_kind=ClientKind.CLI_EPHEMERAL
            )
            await client.connect()
            return client
        except Exception as exc:
            last_exc = exc
            await asyncio.sleep(_CONNECT_RETRY_S)
    raise TimeoutError(
        f"supervisor did not accept connections within {_STARTUP_TIMEOUT_S}s"
    ) from last_exc


async def _run(repo: Path, socket_path: Path) -> None:
    cfg = Config.load(repo)
    deadline = time.monotonic() + _STARTUP_TIMEOUT_S
    client = await _connect_with_retry(repo, socket_path, cfg, deadline=deadline)
    snapshot = await client.get_dispatch_snapshot()
    assert snapshot is not None
    await client.close()


def test_lifecycle(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _scaffold_project(repo)

    # Route the socket into tmp_path so this test is isolated from any live instance.
    env = {
        **os.environ,
        "XDG_RUNTIME_DIR": str(tmp_path),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
    }
    socket_path = tmp_path / "murder" / project_session_name(repo) / "bus.sock"

    proc = subprocess.Popen(
        [sys.executable, "-m", "murder", "serviced"],
        cwd=str(repo),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        asyncio.run(_run(repo, socket_path))
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=_SHUTDOWN_TIMEOUT_S)
        assert proc.returncode in (0, -signal.SIGTERM)
    except Exception:
        output = b""
        if proc.stdout:
            proc.kill()
            proc.wait()
            output = proc.stdout.read()
        pytest.fail(
            f"lifecycle test failed\n--- supervisor output ---\n{output.decode(errors='replace')}"
        )
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
