"""Collect harness usage snapshots for the configured crow harness pool."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, is_dataclass

from murder import tmux
from murder.config import HarnessRoleConfig, resolve_default_crow_startup_model
from murder.harnesses import REGISTRY
from murder.harnesses import get as get_harness
from murder.harnesses.base import HarnessAdapter, HarnessSession
from murder.harnesses.models import HarnessStartSpec, HarnessUsageStatus
from murder.runtime import Runtime
from murder.session_names import format_session_name


def harness_kinds_with_usage_collection(crow_cfg: HarnessRoleConfig) -> list[str]:
    """Ordered unique harness kinds from the crow pool that support usage sampling."""
    pool = list(crow_cfg.harnesses) if crow_cfg.harnesses else [crow_cfg.harness]
    out: list[str] = []
    for kind in dict.fromkeys(pool):
        cls = REGISTRY.get(kind)
        if cls is None:
            continue
        if cls.usage_collection_mode != "none":
            out.append(kind)
    return out


def insert_harness_usage_snapshot(db: sqlite3.Connection, status: HarnessUsageStatus) -> None:
    payload = asdict(status) if is_dataclass(status) else status
    db.execute(
        """
        INSERT INTO harness_usage_snapshots
            (harness, source, fetched_at, status_json)
        VALUES (?, ?, ?, ?)
        """,
        (
            status.harness,
            status.source,
            status.fetched_at,
            json.dumps(payload, sort_keys=True, default=str),
        ),
    )


async def _ensure_tmux_slash_session(
    rt: Runtime, kind: str, startup_model: str | None
) -> HarnessSession | None:
    """Return a ready :class:`HarnessSession` or None if setup failed."""

    name = format_session_name(rt, "usage", f"_{kind}")
    adapter = get_harness(kind, startup_model=startup_model)
    hs = adapter.attach(name, rt.repo_root)

    if await tmux.session_exists(name):
        ready = await hs.wait_ready(timeout_s=90.0)
        if not ready.ok:
            return None
        idle = await hs.wait_idle(timeout_s=30.0)
        if not idle.ok:
            return None
    else:
        spec = HarnessStartSpec(cwd=rt.repo_root, startup_model=startup_model)
        started = await hs.start(spec)
        if not started.ok:
            return None
    return hs


async def sample_harness_usages_for_config(rt: Runtime) -> tuple[int, int]:
    """Start or reuse usage probe sessions, collect usage, persist snapshots.

    Returns (stored_count, failure_count) for notifications.
    """
    db = rt.db
    if db is None:
        return 0, 0

    cfg = rt.config.default_crow
    kinds = harness_kinds_with_usage_collection(cfg)
    stored = 0
    failures = 0

    for kind in kinds:
        cls: type[HarnessAdapter] = REGISTRY[kind]
        mode = cls.usage_collection_mode
        model = resolve_default_crow_startup_model(cfg, None, kind)  # type: ignore[arg-type]

        if mode == "http":
            adapter = get_harness(kind, startup_model=model)
            result = await adapter.collect_usage_status("")
            if not result.ok or result.data is None:
                failures += 1
                continue
            insert_harness_usage_snapshot(db, result.data)
            stored += 1
            continue

        if mode == "tmux_slash":
            hs = await _ensure_tmux_slash_session(rt, kind, model)
            if hs is None:
                failures += 1
                continue
            result = await hs.collect_usage_status()
            if not result.ok or result.data is None:
                failures += 1
                continue
            insert_harness_usage_snapshot(db, result.data)
            stored += 1

    return stored, failures
