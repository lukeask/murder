"""Shared harness usage sampling via the usage-probe worker.

Used by the TUI usage-panel `r` sample key, the headless service interval poll, and any
other caller that should issue the same ``state.harness_usage.sample`` command.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from typing import TYPE_CHECKING
from uuid import uuid4

from murder.bus.protocol import CommandEvent

if TYPE_CHECKING:
    import sqlite3

    from murder.bus.broker import DurableBroker

log = logging.getLogger(__name__)

USAGE_PROBE_TARGET = "usage-probe"
HARNESS_USAGE_SAMPLE_KIND = "state.harness_usage.sample"
USAGE_SAMPLE_POLL_INTERVAL_S = 600.0
USAGE_SAMPLE_POLL_JITTER_FRACTION = 0.20
USAGE_SAMPLE_DEFAULT_TIMEOUT_S = 20.0
COMMAND_POLL_S = 0.05

TRIGGER_USAGE_MANUAL_REFRESH = "manual_refresh"
TRIGGER_USAGE_MANUAL_KEY = TRIGGER_USAGE_MANUAL_REFRESH
TRIGGER_USAGE_SERVICE_INTERVAL = "interval_10m"
USAGE_SAMPLE_SERVICE_INTERVAL_MODES = frozenset({"http"})


def jittered_usage_poll_interval_s(
    *,
    base_s: float = USAGE_SAMPLE_POLL_INTERVAL_S,
    jitter_fraction: float = USAGE_SAMPLE_POLL_JITTER_FRACTION,
) -> float:
    spread = max(0.0, base_s * jitter_fraction)
    return random.uniform(max(0.0, base_s - spread), base_s + spread)


def harness_usage_sample_payload(
    *,
    trigger: str,
    modes: set[str] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {"trigger": trigger}
    if modes is not None:
        payload["modes"] = sorted(modes)
    return payload


async def submit_harness_usage_sample_inprocess(
    broker: DurableBroker,
    db: sqlite3.Connection,
    run_id: str,
    *,
    agent_id: str,
    trigger: str,
    modes: set[str] | None = None,
    timeout_s: float = USAGE_SAMPLE_DEFAULT_TIMEOUT_S,
) -> dict[str, object] | None:
    """Publish a usage sample command and block until the commands row settles.

    Used by harness agents to report usage metrics over the bus.
    """
    payload = harness_usage_sample_payload(trigger=trigger, modes=modes)
    command = CommandEvent(
        run_id=run_id,
        agent_id=agent_id,
        target_worker=USAGE_PROBE_TARGET,
        kind=HARNESS_USAGE_SAMPLE_KIND,
        payload=payload,
        correlation_id=f"{agent_id}-{uuid4()}",
        idempotency_key=f"{agent_id}-{HARNESS_USAGE_SAMPLE_KIND}-{uuid4()}",
    )
    await broker.publish(command)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        row = db.execute(
            "SELECT status, result_json, last_error FROM commands WHERE id = ?",
            (str(command.id),),
        ).fetchone()
        if row is None:
            await asyncio.sleep(COMMAND_POLL_S)
            continue
        status = str(row["status"])
        if status == "done":
            raw = row["result_json"]
            return json.loads(raw) if raw else {}
        if status == "failed":
            log.warning(
                "harness usage sample failed: %s",
                str(row["last_error"] or "unknown"),
            )
            return None
        await asyncio.sleep(COMMAND_POLL_S)
    log.warning("harness usage sample timed out after %ss", timeout_s)
    return None


async def run_service_usage_poll_loop(
    broker: DurableBroker,
    db: sqlite3.Connection,
    run_id: str,
) -> None:
    """Sample http-mode harnesses immediately, then on a jittered interval until cancelled."""

    interval_modes = set(USAGE_SAMPLE_SERVICE_INTERVAL_MODES)
    while True:
        try:
            await submit_harness_usage_sample_inprocess(
                broker,
                db,
                run_id,
                agent_id="usage_poll",
                trigger=TRIGGER_USAGE_SERVICE_INTERVAL,
                modes=interval_modes,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("service usage poll loop iteration failed")
        await asyncio.sleep(jittered_usage_poll_interval_s())
