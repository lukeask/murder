"""Tests for the per-file LLM summarizer (t058).

No network: a stub APIClient returns canned CompletionResults. Coroutines
are driven with asyncio.run() — the repo has no pytest-asyncio convention.
"""

from __future__ import annotations

import asyncio

from murder.codebase_map.summarize import FileSummarizer
from murder.codebase_map.tokens import count_tokens
from murder.llm.clients.base import CompletionResult


class StubClient:
    """Records calls and replays canned (text, completion_tokens) pairs."""

    def __init__(self, replies: list[tuple[str, int]]) -> None:
        self._replies = list(replies)
        self.calls: list[dict] = []

    async def complete(self, **kwargs) -> CompletionResult:
        self.calls.append(kwargs)
        text, completion_tokens = self._replies.pop(0)
        return CompletionResult(
            text=text,
            tool_calls=[],
            prompt_tokens=10,
            completion_tokens=completion_tokens,
            model="stub",
            latency_ms=1.0,
        )


_SRC = "def f(x):\n    return x\n" * 100  # large enough that 15% > floor


def test_budget_is_15pct_with_floor():
    src_tokens = count_tokens(_SRC)
    assert src_tokens > 0
    client = StubClient([("# summary", 5)])
    summarizer = FileSummarizer(client)
    summary = asyncio.run(summarizer.summarize("mod.py", _SRC))

    expected_budget = max((src_tokens * 15 + 99) // 100, 128)
    # max_tokens passed to complete() == the computed budget.
    assert client.calls[0]["max_tokens"] == expected_budget
    assert summary.source_tokens == src_tokens
    assert summary.summary_tokens == 5


def test_tiny_file_gets_floor_budget():
    client = StubClient([("# tiny", 3)])
    summarizer = FileSummarizer(client)
    asyncio.run(summarizer.summarize("tiny.py", "x = 1\n"))
    assert client.calls[0]["max_tokens"] == 128


def test_over_budget_reprompts_once_then_truncates():
    # Both replies report way over the floor budget (128).
    over_text = "word " * 2000
    client = StubClient([(over_text, 9999), (over_text, 9999)])
    summarizer = FileSummarizer(client)
    summary = asyncio.run(summarizer.summarize("tiny.py", "x = 1\n"))

    # Exactly two completion calls: initial + one re-prompt.
    assert len(client.calls) == 2
    # The second call is the tighter re-prompt mentioning N vs M.
    second_user = client.calls[1]["messages"][0]["content"]
    assert "9999" in second_user
    assert "128" in second_user
    # Truncated, never over budget, marker appended.
    assert summary.summary_tokens <= 128
    assert summary.body.endswith("…[truncated]")


def test_under_budget_no_reprompt():
    client = StubClient([("# fits", 4)])
    summarizer = FileSummarizer(client)
    summary = asyncio.run(summarizer.summarize("tiny.py", "x = 1\n"))
    assert len(client.calls) == 1
    assert summary.body == "# fits"


def test_source_hash_stable():
    client = StubClient([("a", 1), ("b", 1)])
    summarizer = FileSummarizer(client)
    s1 = asyncio.run(summarizer.summarize("a.py", "same source\n"))
    s2 = asyncio.run(summarizer.summarize("b.py", "same source\n"))
    assert s1.source_hash == s2.source_hash
    assert len(s1.source_hash) == 64  # sha256 hex


def test_symbols_passed_into_prompt_for_python():
    client = StubClient([("ok", 1)])
    summarizer = FileSummarizer(client)
    asyncio.run(summarizer.summarize("m.py", "def hello(name: str) -> str:\n    return name\n"))
    system = client.calls[0]["system"]
    assert "def hello(name: str) -> str" in system


def test_no_extractor_note_for_non_python():
    client = StubClient([("ok", 1)])
    summarizer = FileSummarizer(client)
    asyncio.run(summarizer.summarize("m.rs", "fn main() {}\n"))
    system = client.calls[0]["system"]
    assert "no programmatic extractor" in system
