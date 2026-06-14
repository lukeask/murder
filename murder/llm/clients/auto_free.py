"""Round-robin, rate-aware failover client for free-tier inference providers.

The free tiers of Groq and Cerebras enforce limits **per model** (each model has
its own RPM/TPM bucket; on Cerebras a 1M-tokens/day ceiling is additionally shared
across models). So the way to get usable throughput from "free" inference is to
spread requests across several models and fail over the instant one is throttled —
not to hammer a single endpoint.

This client does that:

  * a **pool** of (provider, model) specs, ordered best-quality-first per the
    internal summarizer benchmark (rubric v2);
  * **round-robin** rotation of the start index so successive calls land on
    different models' buckets;
  * **proactive pacing** from each model's published RPM (approximate — Groq gates
    exact numbers behind login), so we usually avoid the 429 in the first place;
  * **reactive cooldown** on 429 that honors ``Retry-After`` (and on transient
    transport/5xx errors), which is the real guard: if a published RPM here is
    wrong, the cooldown self-corrects;
  * per-model ``reasoning_effort`` forwarding. gpt-oss is a reasoning model that
    splits ``max_tokens`` between hidden reasoning and visible content; without an
    explicit ``low`` effort it burns the whole cap on reasoning and returns empty
    content. qwen3/glm take ``none``; non-reasoning models omit the knob.

Quality tiering comes from the benchmark: glm-4.7, qwen3-32b, gpt-oss-120b(low) and
llama-3.3-70b are all failover-eligible (faithfulness>=4, continuation>=4);
gpt-oss-20b is last-resort (faithful but intermittently drops continuation-critical
state). gpt-oss-120b appears on BOTH providers because they are separate buckets.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from murder.llm.clients._retry import (
    RATE_LIMITED_STATUS,
    is_retryable_exc,
    retry_after_seconds,
)
from murder.llm.clients.base import APIClient, CompletionResult, ToolSpec

LOGGER = logging.getLogger(__name__)

# Longest we will block waiting for a paced/cooled bucket before giving up on it
# this round (a summary is best-effort; we would rather fail over than stall).
_MAX_PACING_WAIT = 12.0
# Cooldown applied to a bucket that raised a non-rate-limit transient error.
_TRANSIENT_COOLDOWN = 5.0


@dataclass(frozen=True)
class PoolSpec:
    """One (provider, model) failover candidate and its free-tier pacing knobs."""

    provider: str
    model: str
    rpm: float  # published free-tier requests/min (approx); drives proactive pacing
    reasoning_effort: str | None = None  # "low" gpt-oss, "none" qwen3/glm, None to omit

    @property
    def min_interval(self) -> float:
        return 60.0 / self.rpm if self.rpm > 0 else 0.0


# Best-quality-first. RPM values are approximate published free-tier numbers
# (2026-06); they only pace requests — the 429/Retry-After cooldown is authoritative.
_DEFAULT_SPECS: list[PoolSpec] = [
    PoolSpec("groq", "qwen/qwen3-32b", rpm=60, reasoning_effort="none"),
    PoolSpec("cerebras", "zai-glm-4.7", rpm=10, reasoning_effort="none"),
    PoolSpec("groq", "openai/gpt-oss-120b", rpm=30, reasoning_effort="low"),
    PoolSpec("cerebras", "gpt-oss-120b", rpm=10, reasoning_effort="low"),
    PoolSpec("groq", "llama-3.3-70b-versatile", rpm=30, reasoning_effort=None),
    PoolSpec("groq", "openai/gpt-oss-20b", rpm=30, reasoning_effort="low"),  # last resort
]


@dataclass
class _Entry:
    client: APIClient
    spec: PoolSpec
    # monotonic-clock instants gating reuse of this bucket
    cooldown_until: float = 0.0  # set by a 429/transient error
    last_used: float = 0.0  # set on every successful call (for RPM pacing)

    def ready_at(self) -> float:
        """Earliest monotonic time this bucket may be used again."""
        return max(self.cooldown_until, self.last_used + self.spec.min_interval)


def _is_rate_limit(exc: Exception) -> bool:
    return (
        isinstance(exc, httpx.HTTPStatusError)
        and exc.response.status_code == RATE_LIMITED_STATUS
    )


class AutoFreeClient(APIClient):
    """Round-robin failover across a pool of free-tier provider/model buckets."""

    def __init__(self, entries: list[_Entry]) -> None:
        if not entries:
            raise ValueError("AutoFreeClient requires at least one pool entry")
        self._entries = entries
        self._cursor = 0  # rotates so successive calls start on different buckets

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
        # The pool is fixed; the caller's requested model is intentionally ignored
        # (auto_free=True means the pool wins). reasoning_effort comes per-entry.
        if model:
            LOGGER.debug("free-pool ignoring requested model %r; using pool", model)

        n = len(self._entries)
        # Rotate the visit order so load spreads across each model's separate bucket.
        order = [(self._cursor + i) % n for i in range(n)]
        hard_failed: set[int] = set()
        last_exc: Exception | None = None
        attempts = 0
        max_attempts = n * 2  # one spreading lap + one fallback lap, bounded

        queue = list(order)
        while queue and attempts < max_attempts:
            attempts += 1
            i = queue.pop(0)
            if i in hard_failed:
                continue
            entry = self._entries[i]

            wait = entry.ready_at() - time.monotonic()
            if wait > 0:
                # If a different, not-hard-failed bucket is ready right now, prefer it
                # and defer this one to the back of the queue (throughput first).
                if any(
                    j not in hard_failed
                    and self._entries[j].ready_at() <= time.monotonic()
                    for j in queue
                ):
                    queue.append(i)
                    continue
                # Nothing else is ready; wait for this bucket (bounded).
                await asyncio.sleep(min(wait, _MAX_PACING_WAIT))

            extra: dict[str, Any] = {}
            if entry.spec.reasoning_effort is not None:
                extra["reasoning_effort"] = entry.spec.reasoning_effort
            try:
                result = await entry.client.complete(
                    model=entry.spec.model,
                    system=system,
                    messages=messages,
                    tools=tools,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    **extra,
                )
            except Exception as e:  # noqa: BLE001 - one bad client must not kill failover
                last_exc = e
                if self._cool_or_drop(entry, e):
                    queue.append(i)  # cooled; eligible again later this call
                else:
                    hard_failed.add(i)  # terminal; stop using this bucket
                continue
            entry.last_used = time.monotonic()
            self._cursor = (i + 1) % n  # next call begins at the following bucket
            return result

        raise RuntimeError("all free-pool providers failed") from last_exc

    def _cool_or_drop(self, entry: _Entry, exc: Exception) -> bool:
        """Apply a cooldown and return True (requeue) or False (drop) for ``exc``."""
        if _is_rate_limit(exc):
            ra = retry_after_seconds(exc)
            cool = ra if ra is not None else max(entry.spec.min_interval, 60.0)
            entry.cooldown_until = time.monotonic() + cool
            LOGGER.warning("free-pool %s rate-limited; cooling %.1fs", entry.spec.model, cool)
            return True
        if is_retryable_exc(exc):  # transient transport / 5xx
            entry.cooldown_until = time.monotonic() + _TRANSIENT_COOLDOWN
            LOGGER.warning("free-pool %s transient error: %s", entry.spec.model, exc)
            return True
        # Terminal 4xx (400/401/413/…) or an unexpected error: drop this bucket.
        LOGGER.warning("free-pool %s hard-failed: %s", entry.spec.model, exc)
        return False

    @classmethod
    def build_default(cls) -> AutoFreeClient | None:
        """Build the default pool, skipping providers whose keys are unset."""
        from murder.llm.clients import create_client  # noqa: PLC0415 (avoids import cycle)

        clients: dict[str, APIClient | None] = {}
        entries: list[_Entry] = []
        for spec in _DEFAULT_SPECS:
            if spec.provider not in clients:
                clients[spec.provider] = create_client(spec.provider)
            client = clients[spec.provider]
            if client is None:  # provider key missing -> skip its buckets
                continue
            entries.append(_Entry(client=client, spec=spec))
        if not entries:
            return None
        return cls(entries)

    async def aclose(self) -> None:
        seen: set[int] = set()
        for entry in self._entries:
            if id(entry.client) in seen:
                continue
            seen.add(id(entry.client))
            aclose = getattr(entry.client, "aclose", None)
            if aclose is not None:
                await aclose()
