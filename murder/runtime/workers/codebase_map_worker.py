"""Background worker that keeps the codebase map current (t062).

Poll-loop shaped EXACTLY like ``DoneSessionSweeperWorker`` (the conftest patches
``asyncio.sleep`` to a noop, so a sleep-loop would busy-spin — honor the
``wait_for(stop_event.wait(), timeout=interval)`` idiom). Each tick reads HEAD
and calls ``reconcile_map``, which diffs the working tree against the persisted
snapshots PER FILE (by content hash) and does only the work that changed:
(re)summarize drifted/new files, repair missing rendered nodes, prune deleted
ones. When nothing changed it makes zero model calls; when a prior build was
interrupted it resumes from whatever already persisted — so an unfinished map
no longer re-burns the whole API on every launch.

Client selection (locked decision #5): consult ``resolve_tier(user_cfg,
"codebase_map")`` first; tier hit -> client from ``create_client(tier.provider)``
(or ``AutoFreeClient`` when the tier is auto-free), pinned to ``tier.model``;
otherwise / on any failure -> ``AutoFreeClient.build_default()``. If no client at
all -> log "codebase map disabled" once and idle forever. The daemon must NEVER
fail to start over a missing key, so the client is built lazily + fail-soft.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from murder.llm.clients.base import APIClient, CompletionResult, ToolSpec
from murder.runtime.workers.base import Worker, WorkerCtx, WorkerSpec

LOGGER = logging.getLogger(__name__)
MAP_INTERVAL_S = 30.0


class _ModelPinnedClient(APIClient):
    """Wrap an APIClient, forcing every completion onto a fixed model id.

    The :class:`FileSummarizer` / roll-ups pass placeholder model strings
    (``"codebase-map-file"`` etc.); a real provider client needs the configured
    tier model. ``AutoFreeClient`` ignores the model already, so this only
    matters on the explicit-tier path."""

    def __init__(self, inner: APIClient, model: str) -> None:
        self._inner = inner
        self._model = model

    async def complete(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec] | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.0,
        **kwargs: Any,
    ) -> CompletionResult:
        del model
        return await self._inner.complete(
            model=self._model,
            system=system,
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
            temperature=temperature,
            **kwargs,
        )


def _build_client() -> APIClient | None:
    """Build the cheap summarizer client, fail-soft. None -> map disabled."""
    from murder.llm.clients import create_client
    from murder.llm.clients.auto_free import AutoFreeClient

    # Tier consult first (locked decision #5). Fail-soft on every step.
    try:
        from murder.user_config import load_user_config, resolve_tier

        user_cfg = None
        with contextlib.suppress(Exception):
            user_cfg = load_user_config()
        tier = resolve_tier(user_cfg, "codebase_map")
        if tier is not None:
            inner = (
                AutoFreeClient.build_default()
                if tier.auto_free
                else create_client(tier.provider)
            )
            if inner is not None:
                return _ModelPinnedClient(inner, tier.model)
    except Exception:  # pragma: no cover - defensive; never block startup
        LOGGER.exception("codebase map tier resolution failed; using auto-free")

    # Fallback: the built-in free pool.
    return AutoFreeClient.build_default()


class CodebaseMapWorker(Worker):
    def __init__(self, *, interval_s: float = MAP_INTERVAL_S) -> None:
        super().__init__(WorkerSpec(name="codebase-map", heartbeat_s=interval_s))
        self._interval = interval_s
        # Resolved on first tick; ``False`` once we've decided no client exists
        # (logged once, then idle forever). ``None`` = not yet resolved.
        self._summarizer: Any | None = None
        self._disabled = False

    def _ensure_summarizer(self) -> Any | None:
        if self._disabled:
            return None
        if self._summarizer is not None:
            return self._summarizer
        client = _build_client()
        if client is None:
            self._disabled = True
            LOGGER.info("codebase map disabled — no cheap LLM client available")
            return None
        from murder.codebase_map.summarize import FileSummarizer

        self._summarizer = FileSummarizer(client)
        return self._summarizer

    async def run(self, ctx: WorkerCtx, stop_event: asyncio.Event) -> None:
        from murder.codebase_map.build import reconcile_map
        from murder.verdict.enforcement.git_diff import head_commit

        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self._interval)
                return  # stop requested
            except asyncio.TimeoutError:
                pass

            if ctx.db is None:
                continue

            summarizer = self._ensure_summarizer()
            if summarizer is None:
                continue  # disabled; idle (but keep honoring stop_event)

            try:
                # Per-file content-hash reconcile: cheap (zero model calls) when
                # the map is already current, resumable when a prior build was
                # interrupted. This is what stops a never-finishing fresh build
                # from re-burning the API on every launch.
                head = await head_commit(ctx.repo_root)
                await reconcile_map(ctx.repo_root, summarizer, db=ctx.db, head_sha=head)
            except Exception:
                LOGGER.exception("codebase map regeneration failed")
