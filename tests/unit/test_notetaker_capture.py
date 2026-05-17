"""Unit helpers for fenced JSON extraction and capture normalization."""

from __future__ import annotations

import pytest

from murder import notetaker_capture
from murder.clients.base import CompletionResult
from murder.config import NotetakerConfig

EXPECTED_REPROMPT_CALLS = 2


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


def test_capture_metadata_fields_requires_title() -> None:
    with pytest.raises(ValueError):
        notetaker_capture.capture_metadata_fields({"short_vers": "x"})


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


class _TitleClient:
    def __init__(self, *titles: str) -> None:
        self.titles = list(titles)
        self.calls = 0

    async def complete(self, **kwargs):  # type: ignore[no-untyped-def]
        del kwargs
        title = self.titles.pop(0)
        self.calls += 1
        return CompletionResult(
            text=(
                "```json\n"
                f'{{"short_vers":"summary {self.calls}",'
                f'"one_or_two_word_title":"{title}"}}\n'
                "```"
            ),
            tool_calls=[],
            prompt_tokens=0,
            completion_tokens=0,
            model="x",
            latency_ms=0.0,
        )


@pytest.mark.asyncio
async def test_resolve_capture_note_renames_to_slug(memdb, tmp_path) -> None:
    created = notetaker_capture.create_durable_capture(
        repo_root=tmp_path,
        conn=memdb,
        raw="rate limit recovery details",
    )
    out = await notetaker_capture.resolve_capture_note(
        repo_root=tmp_path,
        conn=memdb,
        raw="rate limit recovery details",
        entry_id=int(created["entry_id"]),
        note_name=str(created["note_name"]),
        client=_TitleClient("Rate Limit Recovery"),
        config=NotetakerConfig(),
    )

    assert out["note_name"] == "rate-limit-recovery"
    assert (tmp_path / ".murder" / "notes" / "rate-limit-recovery.md").exists()
    assert not (
        tmp_path / ".murder" / "notes" / f"{created['note_name']}.md"
    ).exists()


@pytest.mark.asyncio
async def test_resolve_capture_note_reprompts_then_suffixes_on_collision(
    memdb, tmp_path
) -> None:
    first = notetaker_capture.create_durable_capture(
        repo_root=tmp_path,
        conn=memdb,
        raw="existing",
    )
    await notetaker_capture.resolve_capture_note(
        repo_root=tmp_path,
        conn=memdb,
        raw="existing",
        entry_id=int(first["entry_id"]),
        note_name=str(first["note_name"]),
        client=_TitleClient("Rate Limit"),
        config=NotetakerConfig(),
    )
    second = notetaker_capture.create_durable_capture(
        repo_root=tmp_path,
        conn=memdb,
        raw="new",
    )
    client = _TitleClient("Rate Limit", "Rate Limit")
    out = await notetaker_capture.resolve_capture_note(
        repo_root=tmp_path,
        conn=memdb,
        raw="new",
        entry_id=int(second["entry_id"]),
        note_name=str(second["note_name"]),
        client=client,
        config=NotetakerConfig(),
    )

    assert client.calls == EXPECTED_REPROMPT_CALLS
    assert out["note_name"] == "rate-limit-2"
    assert (tmp_path / ".murder" / "notes" / "rate-limit-2.md").exists()
