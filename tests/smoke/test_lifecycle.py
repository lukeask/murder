"""Lifecycle smoke: init → serviced supervisor → connect → snapshot → SIGTERM.

This is the project's proof-of-life for the daemon path: it boots a real
``murder serviced`` supervisor in a subprocess, connects a headless application
WebSocket client, retries until the supervisor accepts connections, fetches a
real state snapshot, then
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
from murder.state.storage.service_registry import (
    project_session_name,
    read_service_session,
    service_registry_path,
)

_MANUAL_ENV = "MURDER_MANUAL_SMOKE"
_STARTUP_TIMEOUT_S = 15.0
_CONNECT_RETRY_S = 0.25
_SHUTDOWN_TIMEOUT_S = 10

pytestmark = pytest.mark.smoke


def _skip_unless_manual() -> None:
    if os.environ.get(_MANUAL_ENV) != "1":
        pytest.skip(f"set {_MANUAL_ENV}=1 to run the lifecycle smoke test")


async def _connect_with_retry(repo: Path, *, deadline: float):
    """Retry the typed application hello until the supervisor accepts connections."""
    from aiohttp import ClientSession

    last_exc: Exception | None = None
    while time.monotonic() < deadline:
        try:
            session = read_service_session(service_registry_path(project_session_name(repo)))
            if session is None:
                raise RuntimeError("application service registry is not ready")
            http = ClientSession()
            ws = await http.ws_connect(session.websocket_url, timeout=2.0)
            await ws.send_json(
                {
                    "op": "client.hello",
                    "protocol_version": 1,
                    "client": {"client_id": "lifecycle-smoke", "client_kind": "cli"},
                }
            )
            hello = await ws.receive_json(timeout=2.0)
            if hello.get("op") != "server.hello":
                raise RuntimeError(f"unexpected application hello: {hello!r}")
            return http, ws
        except Exception as exc:  # supervisor not ready yet / registry absent
            last_exc = exc
            await asyncio.sleep(_CONNECT_RETRY_S)
    raise TimeoutError(
        f"supervisor did not accept connections within {_STARTUP_TIMEOUT_S}s"
    ) from last_exc


async def _drive(repo: Path) -> None:
    deadline = time.monotonic() + _STARTUP_TIMEOUT_S
    http, ws = await _connect_with_retry(repo, deadline=deadline)
    try:
        await ws.send_json(
            {
                "op": "request",
                "request_id": "schedule",
                "request": {"kind": "query", "name": "schedule.get", "params": {}},
            }
        )
        reply = await ws.receive_json(timeout=5.0)
        assert reply["op"] == "reply"
        assert reply["result"] is not None
    finally:
        await ws.close()
        await http.close()


def test_lifecycle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _skip_unless_manual()

    repo = tmp_path / "repo"
    repo.mkdir()
    _scaffold_project(repo)

    # Route the runtime registry into tmp_path so this test is isolated from
    # any live instance running against the developer's real repos.
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    env = {
        **os.environ,
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
    }

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
        asyncio.run(_drive(repo))
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
