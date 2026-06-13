"""Lifecycle smoke: init → serviced supervisor → connect → snapshot → SIGTERM.

This is the project's proof-of-life for the daemon path: it boots a real
``murder serviced`` supervisor in a subprocess, connects a headless bus client
(``SocketBusClient``, the same transport the Ink TUI uses), retries until the
supervisor accepts connections, fetches a real state snapshot over the bus, then
SIGTERMs the supervisor and asserts a clean exit.

Opt-in only: gated behind the ``smoke`` marker AND ``MURDER_MANUAL_SMOKE=1`` so a
normal ``pytest`` run never boots a daemon. Run with::

    MURDER_MANUAL_SMOKE=1 pytest -m smoke tests/smoke/test_lifecycle.py
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from murder.app.cli.init_cmd import _scaffold_project
from murder.bus.client import SocketBusClient
from murder.bus.protocol import ClientKind
from murder.state.storage.service_registry import socket_path_for_repo

_MANUAL_ENV = "MURDER_MANUAL_SMOKE"
_STARTUP_TIMEOUT_S = 15.0
_CONNECT_RETRY_S = 0.25
_SHUTDOWN_TIMEOUT_S = 10

pytestmark = pytest.mark.smoke


def _skip_unless_manual() -> None:
    if os.environ.get(_MANUAL_ENV) != "1":
        pytest.skip(f"set {_MANUAL_ENV}=1 to run the lifecycle smoke test")


async def _connect_with_retry(socket_path: Path, *, deadline: float) -> SocketBusClient:
    """Retry health.ping until the supervisor accepts connections or deadline passes."""
    last_exc: Exception | None = None
    while time.monotonic() < deadline:
        client = SocketBusClient(socket_path, client_kind=ClientKind.CLI_EPHEMERAL)
        try:
            await client.request("health.ping", {}, timeout_s=2.0)
            return client
        except Exception as exc:  # supervisor not ready yet / socket absent
            last_exc = exc
            await asyncio.sleep(_CONNECT_RETRY_S)
    raise TimeoutError(
        f"supervisor did not accept connections within {_STARTUP_TIMEOUT_S}s"
    ) from last_exc


async def _drive(socket_path: Path) -> None:
    deadline = time.monotonic() + _STARTUP_TIMEOUT_S
    client = await _connect_with_retry(socket_path, deadline=deadline)
    snapshot = await client.request("state.schedule_snapshot", {}, timeout_s=5.0)
    assert snapshot is not None


def test_lifecycle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _skip_unless_manual()

    repo = tmp_path / "repo"
    repo.mkdir()
    _scaffold_project(repo)

    # Route the runtime socket into tmp_path so this test is isolated from any
    # live instance running against the developer's real repos. Set the var in
    # our own env too (monkeypatch restores it) so `socket_path_for_repo`
    # resolves to the same location the subprocess writes its socket to.
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    env = {
        **os.environ,
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
    }
    socket_path = socket_path_for_repo(repo)

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
        asyncio.run(_drive(socket_path))
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
            "lifecycle smoke failed\n--- supervisor output ---\n"
            + output.decode(errors="replace")
        )
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
