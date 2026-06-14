"""Failover/round-robin behavior of the free-tier pool client."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from murder.llm.clients.auto_free import AutoFreeClient, PoolSpec, _Entry
from murder.llm.clients.base import CompletionResult


class FakeClient:
    """Records calls; can be scripted to raise then succeed."""

    def __init__(self, name: str, *, errors: list[Exception] | None = None) -> None:
        self.name = name
        self.errors = list(errors or [])
        self.calls: list[dict[str, Any]] = []

    async def complete(self, **kwargs: Any) -> CompletionResult:
        self.calls.append(kwargs)
        if self.errors:
            raise self.errors.pop(0)
        return CompletionResult(
            text=f"summary from {self.name}",
            prompt_tokens=1,
            completion_tokens=1,
            model=kwargs["model"],
            latency_ms=1.0,
        )


def _entry(
    client: FakeClient, model: str, *, rpm: float = 6000.0, effort: str | None = None
) -> _Entry:
    # high rpm -> ~no proactive pacing in tests
    return _Entry(client=client, spec=PoolSpec("groq", model, rpm=rpm, reasoning_effort=effort))


def _rate_limited(retry_after: str | None = None) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://example/chat/completions")
    headers = {"retry-after": retry_after} if retry_after else {}
    response = httpx.Response(429, headers=headers, request=request)
    return httpx.HTTPStatusError("429", request=request, response=response)


def test_round_robin_spreads_across_buckets() -> None:
    a, b = FakeClient("a"), FakeClient("b")
    pool = AutoFreeClient([_entry(a, "model-a"), _entry(b, "model-b")])

    r1 = asyncio.run(pool.complete(model="ignored", system="s", messages=[]))
    r2 = asyncio.run(pool.complete(model="ignored", system="s", messages=[]))

    # successive calls land on different buckets (cursor rotates)
    assert r1.text == "summary from a"
    assert r2.text == "summary from b"
    assert len(a.calls) == 1 and len(b.calls) == 1


def test_failover_on_hard_error_uses_next_entry() -> None:
    a = FakeClient("a", errors=[RuntimeError("boom")])
    b = FakeClient("b")
    pool = AutoFreeClient([_entry(a, "model-a"), _entry(b, "model-b")])

    result = asyncio.run(pool.complete(model="ignored", system="s", messages=[]))

    assert result.text == "summary from b"
    assert len(a.calls) == 1 and len(b.calls) == 1


def test_rate_limit_cools_bucket_and_fails_over() -> None:
    a = FakeClient("a", errors=[_rate_limited(retry_after="30")])
    b = FakeClient("b")
    pool = AutoFreeClient([_entry(a, "model-a"), _entry(b, "model-b")])

    result = asyncio.run(pool.complete(model="ignored", system="s", messages=[]))

    assert result.text == "summary from b"
    # the 429'd bucket got a cooldown honoring Retry-After
    assert pool._entries[0].cooldown_until > 0


def test_reasoning_effort_forwarded_per_spec() -> None:
    a = FakeClient("a")
    pool = AutoFreeClient([_entry(a, "openai/gpt-oss-120b", effort="low")])

    asyncio.run(pool.complete(model="ignored", system="s", messages=[]))

    assert a.calls[0]["reasoning_effort"] == "low"
    assert a.calls[0]["model"] == "openai/gpt-oss-120b"


def test_no_reasoning_effort_when_spec_omits_it() -> None:
    a = FakeClient("a")
    pool = AutoFreeClient([_entry(a, "llama-3.3-70b-versatile", effort=None)])

    asyncio.run(pool.complete(model="ignored", system="s", messages=[]))

    assert "reasoning_effort" not in a.calls[0]


def test_all_fail_raises() -> None:
    a = FakeClient("a", errors=[RuntimeError("x")])
    b = FakeClient("b", errors=[RuntimeError("y")])
    pool = AutoFreeClient([_entry(a, "model-a"), _entry(b, "model-b")])

    with pytest.raises(RuntimeError, match="all free-pool providers failed"):
        asyncio.run(pool.complete(model="ignored", system="s", messages=[]))


def test_empty_pool_rejected() -> None:
    with pytest.raises(ValueError):
        AutoFreeClient([])
