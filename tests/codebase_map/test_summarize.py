"""Tests for the per-file LLM summarizer (t058).

No network: a stub APIClient returns canned CompletionResults. Coroutines
are driven with asyncio.run() — the repo has no pytest-asyncio convention.
"""

from __future__ import annotations

import asyncio

from murder.codebase_map.summarize import FileSummarizer
from murder.codebase_map.tokens import REASONING_HEADROOM, count_tokens
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
    # Provider cap == content budget + reasoning headroom (reasoning models
    # spend completion tokens thinking; the content budget is enforced by the
    # prompt + local measurement, not the cap).
    assert client.calls[0]["max_tokens"] == expected_budget + REASONING_HEADROOM
    assert summary.source_tokens == src_tokens
    # Body is measured locally, not from provider completion_tokens (which
    # would include reasoning tokens).
    assert summary.summary_tokens == count_tokens("# summary")


def test_tiny_file_gets_floor_budget():
    client = StubClient([("# tiny", 3)])
    summarizer = FileSummarizer(client)
    asyncio.run(summarizer.summarize("tiny.py", "x = 1\n"))
    assert client.calls[0]["max_tokens"] == 128 + REASONING_HEADROOM


def test_over_budget_reprompts_once_then_truncates():
    # Both replies measure way over the floor budget (128).
    over_text = "word " * 2000
    client = StubClient([(over_text, 9999), (over_text, 9999)])
    summarizer = FileSummarizer(client)
    summary = asyncio.run(summarizer.summarize("tiny.py", "x = 1\n"))

    # Exactly two completion calls: initial + one re-prompt.
    assert len(client.calls) == 2
    # The second call is the tighter re-prompt mentioning N (the locally
    # measured body size) vs M (the budget).
    second_user = client.calls[1]["messages"][0]["content"]
    assert str(count_tokens(over_text.strip())) in second_user
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


def test_empty_reply_retries_once_with_doubled_cap():
    # Reasoning models can spend the whole cap thinking and emit no content
    # (completion_tokens == cap, empty text). One retry at double the cap.
    client = StubClient([("", 128), ("# recovered", 20)])
    summarizer = FileSummarizer(client)
    summary = asyncio.run(summarizer.summarize("tiny.py", "x = 1\n"))

    assert len(client.calls) == 2
    assert client.calls[0]["max_tokens"] == 128 + REASONING_HEADROOM
    assert client.calls[1]["max_tokens"] == (128 + REASONING_HEADROOM) * 2
    assert summary.body == "# recovered"
    assert summary.summary_tokens == count_tokens("# recovered")


def test_empty_reply_twice_yields_honest_empty():
    # Starved twice -> empty body, zero tokens, no tighten/truncate calls.
    client = StubClient([("", 128), ("", 256)])
    summarizer = FileSummarizer(client)
    summary = asyncio.run(summarizer.summarize("tiny.py", "x = 1\n"))

    assert len(client.calls) == 2
    assert summary.body == ""
    assert summary.summary_tokens == 0


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
