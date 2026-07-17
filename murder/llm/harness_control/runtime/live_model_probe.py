"""Disposable live model-catalog probes through verified harness control."""

from __future__ import annotations

import asyncio
import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from murder.llm.harness_control.runtime.session import VerifiedHarnessControlSession
from murder.llm.harnesses import get as get_harness
from murder.llm.harnesses.models import HarnessStartSpec
from murder.runtime.terminal import tmux
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
    session = (
        f"murder_models_{harness_kind}_{os.getpid()}_"
        f"{time.monotonic_ns() % 1_000_000}"
    )
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    init_db(connection)
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
            connection=connection,
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


__all__ = ["LIVE_MODEL_DISCOVERY_HARNESSES", "LiveModelProbeResult", "probe_live_models"]
