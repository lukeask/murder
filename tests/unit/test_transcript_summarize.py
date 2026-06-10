from __future__ import annotations

import asyncio
import json
from typing import Any, cast

import pytest

from murder.llm.harnesses.transcript_summarize import (
    SummaryPrompt,
    build_default_summary_provider,
    build_summary_prompt,
    summarize_doc,
    summarize_transcript,
)


class StubSummaryProvider:
    def __init__(self, response: str = "Agent is editing the summary module.") -> None:
        self.response = response
        self.prompts: list[SummaryPrompt] = []

    async def summarize(self, prompt: SummaryPrompt) -> str:
        self.prompts.append(prompt)
        return self.response


def _payload(prompt: SummaryPrompt) -> dict[str, Any]:
    start = prompt.user.index("{")
    return cast(dict[str, Any], json.loads(prompt.user[start:]))


def test_build_summary_prompt_uses_segments_prior_condensed_and_state() -> None:
    prompt = build_summary_prompt(
        prior_condensed="Agent inspected the parser.",
        state="working",
        segments=[
            {"type": "user", "text": "add summarizer"},
            {
                "type": "plan_update",
                "title": "Updated Plan",
                "items": [{"done": True, "text": "read schema"}],
            },
            {
                "type": "tool_call",
                "title": "sed -n transcripts/core.py",
                "input": "sed -n '1,120p' murder/llm/harnesses/transcripts/core.py",
                "result": None,
                "elided": True,
                "running": False,
            },
            {
                "type": "assistant",
                "phase": "final",
                "text": "The provider seam should wrap APIClient.",
                "elapsed": "1m 02s",
            },
        ],
    )

    assert "latest activity" in prompt.system
    assert "phase=final" in prompt.system
    assert "plan_update" in prompt.system
    assert "tool_call" in prompt.system

    payload = _payload(prompt)
    assert payload["prior_condensed"] == "Agent inspected the parser."
    assert payload["state"] == "working"
    assert payload["segments"][-1] == {
        "type": "assistant",
        "phase": "final",
        "text": "The provider seam should wrap APIClient.",
        "elapsed": "1m 02s",
    }
    assert payload["segments"][2]["title"] == "sed -n transcripts/core.py"


def test_summarize_segments_uses_stubbed_provider_without_network() -> None:
    provider = StubSummaryProvider("Agent added a deterministic summarizer test.")

    summary = asyncio.run(
        summarize_transcript(
            prior_condensed="Agent read the plan.",
            state="awaiting_input",
            segments=[
                {"type": "assistant", "phase": "final", "text": "Tests pass.", "elapsed": None}
            ],
            provider=provider,
        )
    )

    assert summary == "Agent added a deterministic summarizer test."
    assert len(provider.prompts) == 1
    payload = _payload(provider.prompts[0])
    assert payload["prior_condensed"] == "Agent read the plan."
    assert payload["state"] == "awaiting_input"
    assert payload["segments"][0]["text"] == "Tests pass."


def test_summarize_doc_returns_copy_with_condensed() -> None:
    provider = StubSummaryProvider("Agent is waiting for input after finishing.")
    doc = {
        "harness": "codex",
        "state": "awaiting_input",
        "condensed": None,
        "segments": [{"type": "assistant", "phase": "final", "text": "Done.", "elapsed": "13s"}],
    }

    updated = asyncio.run(summarize_doc(doc, provider=provider))

    assert updated is not doc
    assert updated["condensed"] == "Agent is waiting for input after finishing."
    assert doc["condensed"] is None


def test_default_provider_can_be_absent_without_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "")
    monkeypatch.setenv("CEREBRAS_API_KEY", "")

    assert build_default_summary_provider() is None
