"""Tests for the dir/root roll-ups (t059).

No network: a stub APIClient records calls and replays canned text.
Coroutines are driven with asyncio.run() — no pytest-asyncio convention.
"""

from __future__ import annotations

import asyncio

from murder.codebase_map.rollup import dir_summary, root_summary
from murder.llm.clients.base import CompletionResult


class StubClient:
    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.calls: list[dict] = []

    async def complete(self, **kwargs) -> CompletionResult:
        self.calls.append(kwargs)
        return CompletionResult(
            text=self._replies.pop(0),
            tool_calls=[],
            prompt_tokens=10,
            completion_tokens=5,
            model="stub",
            latency_ms=1.0,
        )


def test_dir_summary_renders_children_and_returns_body():
    client = StubClient(["DIR BODY"])
    children = [("a.py", "summary of a"), ("b.py", "summary of b")]
    body = asyncio.run(dir_summary(client, "pkg", children))

    assert body == "DIR BODY"
    system = client.calls[0]["system"]
    assert "pkg" in system
    assert "a.py" in system
    assert "summary of a" in system
    assert "b.py" in system
    assert "summary of b" in system


def test_dir_summary_strips_whitespace():
    client = StubClient(["  padded  \n"])
    body = asyncio.run(dir_summary(client, "pkg", [("x.py", "x")]))
    assert body == "padded"


def test_root_summary_passes_dir_entries():
    client = StubClient(["ROOT BODY"])
    dirs = [("pkg", "pkg dir summary"), ("lib", "lib dir summary")]
    body = asyncio.run(root_summary(client, dirs))

    assert body == "ROOT BODY"
    system = client.calls[0]["system"]
    assert "pkg dir summary" in system
    assert "lib dir summary" in system


def test_rollup_retries_once_on_empty_with_doubled_budget():
    # Reasoning models can burn the whole max_tokens cap before emitting
    # content; an empty reply triggers exactly one retry at double budget.
    client = StubClient(["", "DIR BODY"])
    body = asyncio.run(dir_summary(client, "pkg", [("a.py", "summary of a")]))

    assert body == "DIR BODY"
    assert len(client.calls) == 2
    assert client.calls[1]["max_tokens"] == client.calls[0]["max_tokens"] * 2


def test_rollup_empty_retry_is_bounded():
    # Two empties in a row -> give up with empty body, no third call.
    client = StubClient(["", ""])
    body = asyncio.run(dir_summary(client, "pkg", [("a.py", "x")]))
    assert body == ""
    assert len(client.calls) == 2


def test_rollup_budget_under_inputs():
    # Combined child input is large; the roll-up budget must be a fraction of it.
    big = "word " * 4000
    client = StubClient(["ok"])
    asyncio.run(dir_summary(client, "pkg", [("a.py", big), ("b.py", big)]))
    max_tokens = client.calls[0]["max_tokens"]
    combined_tokens = len((big + "\n\n" + big)) // 4
    assert max_tokens < combined_tokens
