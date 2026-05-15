"""Unit helpers for fenced JSON extraction and capture normalization."""

from __future__ import annotations

import pytest

from murder import notetaker_capture
from murder.clients.base import CompletionResult
from murder.config import NotetakerConfig


def test_extract_json_fence_reads_first_fence() -> None:
    text = '''Some words
```json
{"cleaned":"A","short_vers":"B"}
```
'''
    blob = notetaker_capture.extract_json_fence(text)
    assert blob == {"cleaned": "A", "short_vers": "B"}


def test_extract_json_fence_bare_object() -> None:
    blob = notetaker_capture.extract_json_fence('  {"cleaned":"x","short_vers":"y"} ')
    assert blob == {"cleaned": "x", "short_vers": "y"}


def test_normalized_capture_fields_rejects_sparse() -> None:
    with pytest.raises(ValueError):
        notetaker_capture.normalized_capture_fields({"cleaned": "", "short_vers": "y"})


@pytest.mark.asyncio
async def test_llm_normalized_capture_fallback_on_bad_json() -> None:

    class _Bad:
        async def complete(self, **kwargs):
            del kwargs

            return CompletionResult(
                text="not json at all",
                tool_calls=[],
                prompt_tokens=0,
                completion_tokens=0,
                model="x",
                latency_ms=0.0,
            )

    c, s = await notetaker_capture.llm_normalized_capture(
        raw="  hello world  ",
        system="ignored",
        client=_Bad(),
        config=NotetakerConfig(),
    )
    assert c == "hello world"
    assert "hello world" in s
