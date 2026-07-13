"""Collect harness usage snapshots for the configured crow harness pool."""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Literal, Protocol

from murder.config import Config, HarnessRoleConfig
from murder.llm.harnesses import REGISTRY
from murder.llm.harnesses import get as get_harness
from murder.llm.harnesses.base import HarnessAdapter
from murder.llm.harnesses.models import HarnessUsageFreshness, HarnessUsageStatus

_log = logging.getLogger(__name__)

class _SessionNameScope(Protocol):
    @property
    def config(self) -> Config: ...


class _LiveUsageAgent(Protocol):
    harness: HarnessAdapter


class _VerifiedUsageCapability(Protocol):
    """The semantic usage entry point exposed by verified control sessions."""

    async def collect_usage(self, *, trigger: str) -> HarnessUsageStatus | None: ...


LiveSessionUsageOutcome = Literal["stored", "skipped", "noop", "failed"]


@dataclass(frozen=True, slots=True)
class LiveSessionUsageResult:
    outcome: LiveSessionUsageOutcome
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class UsageSamplingContext:
    """Explicit deps for usage sampling (no Runtime service locator)."""

    config: Config
    repo_root: Path
    db: sqlite3.Connection | None


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


def harness_kinds_to_sample(
    ctx: UsageSamplingContext | _SessionNameScope,
    *,
    modes: set[str] | None = None,
) -> list[str]:
    """Harness kinds to sample: crow pool plus collaborator harness when supported."""
    config = ctx.config
    kinds = harness_kinds_with_usage_collection(config.default_crow)
    collab = config.collaborator.harness
    if _supports_usage(collab) and collab not in kinds:
        kinds.append(collab)
    if modes is None:
        return kinds
    return [
        kind
        for kind in kinds
        if (cls := REGISTRY.get(kind)) is not None and cls.usage_collection_mode in modes
    ]


def insert_harness_usage_snapshot(db: sqlite3.Connection, status: HarnessUsageStatus) -> None:
    freshness = getattr(status.freshness, "value", status.freshness)
    if freshness != HarnessUsageFreshness.CURRENT.value:
        # A diagnostic stale sample is useful to the live controller, but a
        # scheduler snapshot has historically implied a current quota value.
        # Do not silently promote it by persisting it in that table.
        return
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


async def sample_harness_usages(
    ctx: UsageSamplingContext,
    *,
    modes: set[str] | None = None,
) -> tuple[int, int]:
    """Persist side-channel usage without creating or controlling terminal panes.

    Terminal-visible usage is collected only by a bound verified controller for
    an existing harness session.  This background sampler deliberately retains
    HTTP collection and skips ``tmux_slash`` harnesses rather than creating an
    independent probe session that would issue slash commands and key effects.
    """
    db = ctx.db
    if db is None:
        return 0, 0

    kinds = harness_kinds_to_sample(ctx, modes=modes)
    stored = 0
    failures = 0

    for kind in kinds:
        cls: type[HarnessAdapter] = REGISTRY[kind]
        mode = cls.usage_collection_mode
        if mode == "http":
            adapter = get_harness(kind)
            result = await adapter.collect_usage_status("")
            if not result.ok or result.data is None:
                failures += 1
                continue
            if getattr(result.data.freshness, "value", result.data.freshness) == "current":
                insert_harness_usage_snapshot(db, result.data)
                stored += 1
            continue

    return stored, failures


async def sample_live_session_usage(
    agent: _LiveUsageAgent,
    ctx: UsageSamplingContext,
    trigger: str,
) -> LiveSessionUsageResult:
    """Request live usage from the session's verified capability only.

    This module intentionally has no tmux transport dependency.  An agent that
    has not yet been bound to a verified usage capability is skipped instead of
    being controlled through its legacy adapter or an ad-hoc overlay dismissal.
    """
    try:
        cls = REGISTRY.get(agent.harness.kind)
        if cls is None or cls.usage_collection_mode != "tmux_slash":
            return LiveSessionUsageResult(outcome="noop", reason="unsupported_harness")

        db = ctx.db
        if db is None:
            return LiveSessionUsageResult(outcome="failed", reason="no_db")

        control = getattr(agent, "verified_harness_control", None)
        collect_usage = getattr(control, "collect_usage", None)
        if not callable(collect_usage):
            return LiveSessionUsageResult(outcome="skipped", reason="verified_usage_unavailable")

        status = await collect_usage(trigger=trigger)
        if status is None:
            return LiveSessionUsageResult(outcome="failed", reason="verified_usage_unavailable")
        status.raw = dict(status.raw)
        status.raw["trigger"] = trigger
        if getattr(status.freshness, "value", status.freshness) != "current":
            return LiveSessionUsageResult(outcome="skipped", reason="usage_not_current")
        insert_harness_usage_snapshot(db, status)
        return LiveSessionUsageResult(outcome="stored")
    except Exception:
        _log.exception(
            "live usage capture raised for harness=%s trigger=%s",
            getattr(getattr(agent, "harness", None), "kind", "?"),
            trigger,
        )
        return LiveSessionUsageResult(outcome="failed", reason="exception")


__all__ = [
    "LiveSessionUsageResult",
    "UsageSamplingContext",
    "harness_kinds_to_sample",
    "harness_kinds_with_usage_collection",
    "insert_harness_usage_snapshot",
    "sample_harness_usages",
    "sample_live_session_usage",
]
