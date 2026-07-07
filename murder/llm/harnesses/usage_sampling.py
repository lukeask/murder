"""Collect harness usage snapshots for the configured crow harness pool."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import sqlite3
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol

_log = logging.getLogger(__name__)

from murder.runtime.terminal import tmux
from murder.config import Config, HarnessRoleConfig, resolve_default_crow_startup_model
from murder.llm.harnesses import REGISTRY
from murder.llm.harnesses import get as get_harness
from murder.llm.harnesses.base import HarnessAdapter, HarnessSession
from murder.llm.harnesses.models import HarnessStartSpec, HarnessUsageStatus
from murder.llm.harnesses.results import SimpleResult
from murder.state.persistence.usage import (
    clear_usage_probe_session_id,
    get_usage_probe_session_id,
    set_usage_probe_session_id,
)
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


class _LiveUsageAgent(Protocol):
    harness: HarnessAdapter
    harness_session: HarnessSession

    @property
    def _producer(self) -> Any: ...


LiveSessionUsageOutcome = Literal["stored", "skipped", "noop", "failed"]


@dataclass(frozen=True, slots=True)
class LiveSessionUsageResult:
    outcome: LiveSessionUsageOutcome
    reason: str | None = None


_LIVE_IDLE_TIMEOUT_S = 3.0
_LIVE_DISMISS_DELAY_S = 0.15


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
    *,
    resume_session_id: str | None = None,
) -> HarnessSession | None:
    name = format_session_name(ctx, "usage", f"_{kind}")
    adapter = get_harness(kind, startup_model=startup_model)
    with contextlib.suppress(tmux.TmuxError):
        await tmux.kill_session(name)
    hs = adapter.attach(name, ctx.repo_root)
    ready_timeout_s = 8.0 if resume_session_id else 240.0
    spec = HarnessStartSpec(
        cwd=ctx.repo_root,
        startup_model=startup_model,
        resume_session_id=resume_session_id,
        ready_timeout_s=ready_timeout_s,
    )
    started = await hs.start(spec)
    if not started.ok:
        return None
    return hs


async def _capture_resume_failure_pane(session: str) -> str:
    with contextlib.suppress(tmux.TmuxError):
        return await tmux.capture_pane(session, lines=120)
    return ""


async def _capture_graceful_resume_id(hs: HarnessSession) -> str | None:
    exit_cmd = hs.adapter.graceful_exit_command()
    if exit_cmd is None:
        return None
    try:
        await tmux.send_keys(hs.session, exit_cmd)
        await asyncio.sleep(0.5)
        pane = await tmux.capture_pane(hs.session, lines=80)
    except tmux.TmuxError:
        return None
    return hs.adapter.extract_resume_session_id(pane)


def _usage_status_session_id(status: HarnessUsageStatus) -> str | None:
    value = status.raw.get("session_id") if isinstance(status.raw, dict) else None
    return value if isinstance(value, str) and value.strip() else None


async def _sample_tmux_slash_once(
    ctx: UsageSamplingContext,
    kind: str,
    startup_model: str | None,
    *,
    resume_session_id: str | None,
) -> tuple[SimpleResult[HarnessUsageStatus] | None, bool]:
    """Return (usage result, invalid_cached_resume)."""
    hs = await _start_tmux_slash_session(
        ctx,
        kind,
        startup_model,
        resume_session_id=resume_session_id,
    )
    if hs is None:
        name = format_session_name(ctx, "usage", f"_{kind}")
        pane = await _capture_resume_failure_pane(name)
        adapter = get_harness(kind, startup_model=startup_model)
        if resume_session_id and adapter.detects_invalid_resume(pane):
            with contextlib.suppress(tmux.TmuxError):
                await tmux.kill_session(name)
            return None, True
        return None, False
    try:
        result = await hs.collect_usage_status()
        if result.ok and result.data is not None and ctx.db is not None:
            if session_id := _usage_status_session_id(result.data):
                set_usage_probe_session_id(ctx.db, kind, session_id)
            elif session_id := await _capture_graceful_resume_id(hs):
                set_usage_probe_session_id(ctx.db, kind, session_id)
        return result, False
    finally:
        with contextlib.suppress(tmux.TmuxError):
            await tmux.kill_session(hs.session)


async def sample_harness_usages(
    ctx: UsageSamplingContext,
    *,
    modes: set[str] | None = None,
) -> tuple[int, int]:
    """Collect harness usage snapshots, using fresh probe sessions when needed."""
    db = ctx.db
    if db is None:
        return 0, 0

    cfg = ctx.config.default_crow
    kinds = harness_kinds_to_sample(ctx, modes=modes)
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
            cached_session_id = get_usage_probe_session_id(db, kind)
            result, invalid_resume = await _sample_tmux_slash_once(
                ctx,
                kind,
                model,
                resume_session_id=cached_session_id,
            )
            if invalid_resume:
                clear_usage_probe_session_id(db, kind)
                result, _ = await _sample_tmux_slash_once(
                    ctx,
                    kind,
                    model,
                    resume_session_id=None,
                )
            if result is None or not result.ok or result.data is None:
                failures += 1
                continue
            insert_harness_usage_snapshot(db, result.data)
            stored += 1

    return stored, failures


@contextlib.asynccontextmanager
async def usage_capture_projection_guard(agent: _LiveUsageAgent):
    """Suspend pane projection while a live-session usage overlay is open."""
    setattr(agent, "usage_capture_in_progress", True)
    try:
        yield
    finally:
        setattr(agent, "usage_capture_in_progress", False)


async def _live_session_idle(agent: _LiveUsageAgent, hs: HarnessSession) -> bool:
    producer = getattr(agent, "_producer", None)
    if producer is not None and producer.last_state is not None:
        return producer.last_state == "awaiting_input"
    idle = await hs.wait_idle(timeout_s=_LIVE_IDLE_TIMEOUT_S)
    return idle.ok


async def _dismiss_live_usage_overlay(hs: HarnessSession) -> None:
    try:
        await hs.adapter.interrupt_generation(hs.session)
        await asyncio.sleep(_LIVE_DISMISS_DELAY_S)
        if hs.adapter.kind == "codex":
            await tmux.send_keys(hs.session, "Escape", literal=False, enter=False)
            await asyncio.sleep(_LIVE_DISMISS_DELAY_S)
    except Exception:
        _log.debug(
            "failed to dismiss usage overlay for harness=%s session=%s",
            hs.adapter.kind,
            hs.session,
            exc_info=True,
        )


async def sample_live_session_usage(
    agent: _LiveUsageAgent,
    ctx: UsageSamplingContext,
    trigger: str,
) -> LiveSessionUsageResult:
    """Capture usage from an agent's live tmux session without a probe session."""
    try:
        cls = REGISTRY.get(agent.harness.kind)
        if cls is None or cls.usage_collection_mode != "tmux_slash":
            return LiveSessionUsageResult(outcome="noop", reason="unsupported_harness")

        db = ctx.db
        if db is None:
            return LiveSessionUsageResult(outcome="failed", reason="no_db")

        hs = agent.harness_session
        if not await _live_session_idle(agent, hs):
            return LiveSessionUsageResult(outcome="skipped", reason="not_idle")

        async with usage_capture_projection_guard(agent):
            result = await hs.collect_usage_status()
            await _dismiss_live_usage_overlay(hs)

        if not result.ok or result.data is None:
            _log.warning(
                "live usage capture failed for harness=%s trigger=%s: %s",
                agent.harness.kind,
                trigger,
                result.message,
            )
            return LiveSessionUsageResult(outcome="failed", reason=result.message)

        status = result.data
        status.raw = dict(status.raw)
        status.raw["trigger"] = trigger
        insert_harness_usage_snapshot(db, status)
        return LiveSessionUsageResult(outcome="stored")
    except Exception:
        _log.exception(
            "live usage capture raised for harness=%s trigger=%s",
            getattr(getattr(agent, "harness", None), "kind", "?"),
            trigger,
        )
        return LiveSessionUsageResult(outcome="failed", reason="exception")


async def sample_harness_usages_for_config(
    rt: _RuntimeDbScope | UsageSamplingContext,
) -> tuple[int, int]:
    """Compatibility entry: accept Runtime during migration or explicit context."""
    if isinstance(rt, UsageSamplingContext):
        return await sample_harness_usages(rt)
    return await sample_harness_usages(UsageSamplingContext.from_runtime(rt))


__all__ = [
    "LiveSessionUsageResult",
    "UsageSamplingContext",
    "harness_kinds_to_sample",
    "harness_kinds_with_usage_collection",
    "insert_harness_usage_snapshot",
    "sample_harness_usages",
    "sample_harness_usages_for_config",
    "sample_live_session_usage",
    "usage_capture_projection_guard",
]
