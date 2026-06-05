"""Small-LLM summaries for typed transcript v2 documents."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from murder.llm.clients import AutoFreeClient, create_client
from murder.llm.clients.base import APIClient
from murder.llm.harnesses.transcript_v2 import SEGMENT_TYPES
from murder.llm.prompts import load as load_prompt

_log = logging.getLogger(__name__)

Segment = Mapping[str, Any]

DEFAULT_SUMMARY_MODEL = "transcript-summary"
LOCAL_SUMMARY_MODEL = "local"
MAX_SUMMARY_TOKENS = 120


@dataclass(frozen=True)
class SummaryPrompt:
    """Messages sent to the cheap summary provider."""

    system: str
    user: str


class SummaryProvider(Protocol):
    """Backend capable of producing a cheap, fast transcript summary."""

    async def summarize(self, prompt: SummaryPrompt) -> str:
        """Return a one-to-two sentence summary for ``prompt``."""


class APITranscriptSummaryProvider:
    """Summary provider backed by the existing normalized APIClient interface."""

    def __init__(
        self,
        client: APIClient,
        *,
        model: str = DEFAULT_SUMMARY_MODEL,
        max_tokens: int = MAX_SUMMARY_TOKENS,
    ) -> None:
        self.client = client
        self.model = model
        self.max_tokens = max_tokens

    async def summarize(self, prompt: SummaryPrompt) -> str:
        result = await self.client.complete(
            model=self.model,
            system=prompt.system,
            messages=[{"role": "user", "content": prompt.user}],
            tools=None,
            max_tokens=self.max_tokens,
            temperature=0.0,
        )
        return _clean_summary(result.text)


def build_default_summary_provider(*, prefer_local: bool = False) -> SummaryProvider | None:
    """Build the default cheap summary provider.

    By default this fronts the existing auto-free Groq/Cerebras failover pool.
    ``prefer_local`` is the extension point for a local OpenAI-compatible backend:
    callers can opt into it once local summary serving is configured.
    """

    if prefer_local:
        local = create_client("local")
        if local is not None:
            return APITranscriptSummaryProvider(local, model=LOCAL_SUMMARY_MODEL)
    client = AutoFreeClient.build_default()
    if client is None:
        return None
    return APITranscriptSummaryProvider(client)


def build_summary_prompt(
    *,
    segments: Iterable[Segment],
    state: str,
    prior_condensed: str | None = None,
) -> SummaryPrompt:
    """Build the deterministic prompt from typed segments and transcript state."""

    system = load_prompt("transcript_summary")
    payload = {
        "prior_condensed": prior_condensed or None,
        "state": state,
        "segments": [_segment_for_prompt(segment) for segment in segments],
    }
    user = (
        "Update the condensed transcript line from this typed transcript payload.\n"
        "The segments are ordered oldest to newest; weight the newest final, plan_update, "
        "and tool_call activity most heavily.\n\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)}"
    )
    return SummaryPrompt(system=system, user=user)


async def summarize_segments(
    *,
    segments: Iterable[Segment],
    state: str,
    prior_condensed: str | None = None,
    provider: SummaryProvider | None = None,
) -> str | None:
    """Summarize typed transcript segments into a condensed line."""

    selected_provider = provider if provider is not None else build_default_summary_provider()
    if selected_provider is None:
        return prior_condensed
    prompt = build_summary_prompt(
        segments=segments,
        state=state,
        prior_condensed=prior_condensed,
    )
    summary = await selected_provider.summarize(prompt)
    return summary or prior_condensed


async def summarize_transcript(
    *,
    segments: Iterable[Segment],
    state: str,
    prior_condensed: str | None = None,
    provider: SummaryProvider | None = None,
) -> str | None:
    """Public alias for callers asking for a cheap fast transcript summary."""

    return await summarize_segments(
        segments=segments,
        state=state,
        prior_condensed=prior_condensed,
        provider=provider,
    )


async def summarize_doc(
    doc: Mapping[str, Any],
    *,
    provider: SummaryProvider | None = None,
) -> dict[str, Any]:
    """Return a copy of a TranscriptDoc with ``condensed`` populated when possible."""

    updated = dict(doc)
    condensed = await summarize_segments(
        segments=_segment_sequence(doc.get("segments")),
        state=str(doc.get("state", "working")),
        prior_condensed=_optional_str(doc.get("condensed")),
        provider=provider,
    )
    updated["condensed"] = condensed
    return updated


def _segment_sequence(value: Any) -> Sequence[Segment]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _optional_str(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _segment_for_prompt(segment: Segment) -> dict[str, Any]:
    segment_type = str(segment.get("type", ""))
    wanted: tuple[str, ...]
    if segment_type == "assistant":
        wanted = ("type", "phase", "text", "elapsed")
    elif segment_type == "tool_call":
        wanted = ("type", "title", "input", "result", "elided", "running")
    elif segment_type == "plan_update":
        wanted = ("type", "title", "items")
    elif segment_type == "agent_event":
        wanted = ("type", "name", "status", "elapsed")
    elif segment_type == "choice_prompt":
        wanted = ("type", "question", "options", "footer", "answered", "chosen")
    else:
        if segment_type not in SEGMENT_TYPES:
            _log.warning("transcript summary: unknown segment type %r", segment_type)
        wanted = ("type", "text")
    return {key: segment[key] for key in wanted if key in segment}


def _clean_summary(text: str | None) -> str:
    cleaned = (text or "").strip()
    if cleaned.startswith('"') and cleaned.endswith('"'):
        cleaned = cleaned[1:-1].strip()
    return " ".join(cleaned.split())


__all__ = [
    "APITranscriptSummaryProvider",
    "SummaryPrompt",
    "SummaryProvider",
    "build_default_summary_provider",
    "build_summary_prompt",
    "summarize_doc",
    "summarize_segments",
    "summarize_transcript",
]
