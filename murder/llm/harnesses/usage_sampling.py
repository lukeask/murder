"""Collect harness usage snapshots for the configured crow harness pool."""

from __future__ import annotations

import contextlib
import json
import sqlite3
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from murder.runtime.terminal import tmux
from murder.config import Config, HarnessRoleConfig, resolve_default_crow_startup_model
from murder.llm.harnesses import REGISTRY
from murder.llm.harnesses import get as get_harness
from murder.llm.harnesses.base import HarnessAdapter, HarnessSession
from murder.llm.harnesses.models import HarnessStartSpec, HarnessUsageStatus
from murder.runtime.terminal.session_names import format_session_name

class _RuntimeDbScope(Protocol):
    """Narrow surface for building :class:`UsageSamplingContext` without importing Runtime."""

    @property
    def config(self) -> Config: ...

    @property
    def repo_root(self) -> Path: ...

    @property
    def db(self) -> sqlite3.Connection | None: ...


class _SessionNameScope(Protocol):
    @property
    def config(self) -> Config: ...


@dataclass(frozen=True, slots=True)
class UsageSamplingContext:
    """Explicit deps for usage sampling (no Runtime service locator)."""

    config: Config
    repo_root: Path
    db: sqlite3.Connection | None

    @classmethod
    def from_runtime(cls, scope: _RuntimeDbScope) -> UsageSamplingContext:
        return cls(config=scope.config, repo_root=scope.repo_root, db=scope.db)


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


def _supports_usage(kind: str) -> bool:
    cls = REGISTRY.get(kind)
    return cls is not None and cls.usage_collection_mode != "none"


def harness_kinds_to_sample(ctx: UsageSamplingContext | _SessionNameScope) -> list[str]:
    """Harness kinds to sample: crow pool plus collaborator harness when supported."""
    config = ctx.config
    kinds = harness_kinds_with_usage_collection(config.default_crow)
    collab = config.collaborator.harness
    if _supports_usage(collab) and collab not in kinds:
        kinds.append(collab)
    return kinds


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


async def _start_tmux_slash_session(
    ctx: UsageSamplingContext,
    kind: str,
    startup_model: str | None,
) -> HarnessSession | None:
    name = format_session_name(ctx, "usage", f"_{kind}")
    adapter = get_harness(kind, startup_model=startup_model)
    # Usage sampling is one-shot on purpose: a fresh session avoids stale
    # slash-command overlays and harnesses that degrade after long runtimes.
    with contextlib.suppress(tmux.TmuxError):
        await tmux.kill_session(name)
    hs = adapter.attach(name, ctx.repo_root)
    spec = HarnessStartSpec(cwd=ctx.repo_root, startup_model=startup_model)
    started = await hs.start(spec)
    if not started.ok:
        return None
    return hs


async def sample_harness_usages(ctx: UsageSamplingContext) -> tuple[int, int]:
    """Collect harness usage snapshots, using fresh probe sessions when needed."""
    db = ctx.db
    if db is None:
        return 0, 0

    cfg = ctx.config.default_crow
    kinds = harness_kinds_to_sample(ctx)
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
            hs = await _start_tmux_slash_session(ctx, kind, model)
            if hs is None:
                failures += 1
                continue
            try:
                result = await hs.collect_usage_status()
                if not result.ok or result.data is None:
                    failures += 1
                    continue
                insert_harness_usage_snapshot(db, result.data)
                stored += 1
            finally:
                with contextlib.suppress(tmux.TmuxError):
                    await tmux.kill_session(hs.session)

    return stored, failures


async def sample_harness_usages_for_config(
    rt: _RuntimeDbScope | UsageSamplingContext,
) -> tuple[int, int]:
    """Compatibility entry: accept Runtime during migration or explicit context."""
    if isinstance(rt, UsageSamplingContext):
        return await sample_harness_usages(rt)
    return await sample_harness_usages(UsageSamplingContext.from_runtime(rt))


__all__ = [
    "UsageSamplingContext",
    "harness_kinds_to_sample",
    "harness_kinds_with_usage_collection",
    "insert_harness_usage_snapshot",
    "sample_harness_usages",
    "sample_harness_usages_for_config",
]
