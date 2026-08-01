"""Disposable live model-catalog probes through verified harness control."""

from __future__ import annotations

import asyncio
import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from murder.llm.harness_control.app_server.client import AppServerClient
from murder.llm.harness_control.app_server.connection import AppServerConnection
from murder.llm.harness_control.runtime.session import VerifiedHarnessControlSession
from murder.llm.harnesses import get as get_harness
from murder.llm.harnesses.models import HarnessStartSpec
from murder.runtime.terminal import tmux
from murder.state.persistence.connection import RepoDb
from murder.state.persistence.schema import init_db

LIVE_MODEL_DISCOVERY_HARNESSES = frozenset({"codex", "cursor", "antigravity"})


@dataclass(frozen=True, slots=True)
class LiveModelProbeResult:
    ok: bool
    models: tuple[tuple[str, str], ...]
    message: str | None = None


async def probe_live_models(
    harness_kind: str,
    cwd: Path,
    *,
    timeout_s: float = 150.0,
) -> LiveModelProbeResult:
    """Start one temporary CLI and exhaustively read its interactive `/model` picker."""

    if harness_kind not in LIVE_MODEL_DISCOVERY_HARNESSES:
        return LiveModelProbeResult(
            False,
            (),
            f"live model discovery is not wrapped for {harness_kind}",
        )
    if harness_kind == "codex":
        return await _probe_codex_app_server_models(cwd, timeout_s=timeout_s)
    session = (
        f"murder_models_{harness_kind}_{os.getpid()}_"
        f"{time.monotonic_ns() % 1_000_000}"
    )
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    init_db(connection)
    db = RepoDb(connection, str(uuid4()))
    try:
        adapter = get_harness(harness_kind)
        started = await asyncio.wait_for(
            adapter.attach(session, cwd).start(
                HarnessStartSpec(cwd=cwd, ready_timeout_s=min(timeout_s, 45.0))
            ),
            timeout=timeout_s,
        )
        if not started.ok:
            return LiveModelProbeResult(False, (), started.message or "harness did not start")
        control = VerifiedHarnessControlSession.from_tmux(
            harness_kind=harness_kind,
            terminal_session=session,
            db=db,
            persistence_session_id=f"live-model-probe:{session}",
        )
        result = await asyncio.wait_for(
            control.discover_models(deadline=timedelta(seconds=timeout_s - 5.0)),
            timeout=timeout_s,
        )
        return LiveModelProbeResult(
            result.succeeded,
            result.models,
            None if result.succeeded else "interactive model discovery did not converge",
        )
    except (TimeoutError, asyncio.TimeoutError):
        return LiveModelProbeResult(False, (), "live model discovery timed out")
    except Exception as exc:  # noqa: BLE001 - probe failures are returned as data
        return LiveModelProbeResult(False, (), f"live model discovery failed: {exc}")
    finally:
        connection.close()
        await tmux.kill_session(session)


async def _probe_codex_app_server_models(cwd: Path, *, timeout_s: float) -> LiveModelProbeResult:
    """Read Codex's authoritative, account-specific catalog without a TUI picker."""
    connection = AppServerConnection(cwd=str(cwd), request_timeout_s=timeout_s)
    try:
        await asyncio.wait_for(connection.start(), timeout=timeout_s)
        client = AppServerClient(connection)
        await asyncio.wait_for(client.initialize(), timeout=timeout_s)
        cursor: str | None = None
        models: list[tuple[str, str]] = []
        while True:
            page = await asyncio.wait_for(
                client.model_list(cursor=cursor), timeout=timeout_s
            )
            entries = page.get("data")
            if not isinstance(entries, list):
                return LiveModelProbeResult(
                    False, (), "app-server model/list returned invalid data"
                )
            for entry in entries:
                if not isinstance(entry, dict) or entry.get("hidden") is True:
                    continue
                model_id = entry.get("id")
                label = entry.get("displayName")
                if isinstance(model_id, str) and model_id.strip():
                    cleaned_id = model_id.strip()
                    cleaned_label = (
                        label.strip()
                        if isinstance(label, str) and label.strip()
                        else cleaned_id
                    )
                    models.append((cleaned_id, cleaned_label))
            next_cursor = page.get("nextCursor")
            if not isinstance(next_cursor, str) or not next_cursor.strip():
                break
            if next_cursor == cursor:
                return LiveModelProbeResult(
                    False, (), "app-server model/list returned a repeated cursor"
                )
            cursor = next_cursor
        if not models:
            return LiveModelProbeResult(
                False, (), "app-server model/list returned no visible models"
            )
        return LiveModelProbeResult(True, tuple(models))
    except (TimeoutError, asyncio.TimeoutError):
        return LiveModelProbeResult(False, (), "app-server model discovery timed out")
    except Exception as exc:  # noqa: BLE001 - probe failures are returned as data
        return LiveModelProbeResult(False, (), f"app-server model discovery failed: {exc}")
    finally:
        await connection.aclose()


__all__ = ["LIVE_MODEL_DISCOVERY_HARNESSES", "LiveModelProbeResult", "probe_live_models"]
