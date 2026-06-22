"""Small-LLM summaries for typed transcript v2 documents."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from murder.llm.clients import AutoFreeClient, create_client
from murder.llm.clients.base import APIClient
from murder.llm.harnesses.transcripts import SEGMENT_TYPES
from murder.llm.prompts import load as load_prompt

_log = logging.getLogger(__name__)

Segment = Mapping[str, Any]

DEFAULT_SUMMARY_MODEL = "transcript-summary"
LOCAL_SUMMARY_MODEL = "local"
# 256, not 120: the old cap truncated summaries mid-sentence and — on reasoning models
# (gpt-oss) that spend the cap on hidden reasoning — left content empty, silently
# falling back to prior_condensed. The summarizer benchmark measured 256 as the point
# where visible output is complete without wasting budget. The pool sets reasoning_effort
# per model so reasoning does not eat this cap.
MAX_SUMMARY_TOKENS = 256


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
    """Build the deterministic prompt from typed segments and transcript state.

    Tool-call segments are reduced to a short *descriptor* (title + an
    "edited file X"/"ran Y" verb + status) — never the full ``input``/``result``
    payloads — so the chunk summary stays cheap and the model is never tempted to
    quote raw command output.
    """

    system = load_prompt("transcript_summary")
    payload = {
        "prior_condensed": prior_condensed or None,
        "state": state,
        "segments": [_segment_for_prompt(segment) for segment in segments],
    }
    user = (
        "Summarize this chunk of intermediate transcript activity into one or two "
        "plain sentences.\n"
        "The segments are ordered oldest to newest; weight the newest plan_update and "
        "tool_call activity most heavily. Tool calls are given as descriptors, not raw "
        "payloads.\n\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)}"
    )
    return SummaryPrompt(system=system, user=user)


def tool_call_descriptor(segment: Segment) -> str:
    """Reduce a ``tool_call`` segment to a compact human descriptor string.

    Spec: title + an "edited file X"/"ran Y" verb + a status. The full
    ``input``/``result`` payloads are intentionally dropped — only enough to
    say *what happened*, not the payload itself. Deterministic for a given
    segment so chunk hashing/dedup stays stable.
    """

    title = str(segment.get("title") or "").strip()
    lowered = title.lower()
    verb = ""
    # Cheap intent classification from the title verb. Bias toward "ran".
    if any(lowered.startswith(p) for p in ("edit", "write", "apply", "patch", "update file")):
        verb = "edited"
    elif lowered.startswith("read") or lowered.startswith("view") or lowered.startswith("cat"):
        verb = "read"
    elif lowered.startswith("search") or lowered.startswith("grep") or lowered.startswith("find"):
        verb = "searched"
    else:
        verb = "ran"

    if segment.get("running"):
        status = "running"
    elif segment.get("elided"):
        status = "no result captured"
    else:
        status = "completed"

    descriptor = f"{verb} {title}".strip() if title else verb
    return f"{descriptor} ({status})"


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


async def summarize_chunk(
    *,
    segments: Sequence[Segment],
    state: str = "working",
    provider: SummaryProvider | None = None,
) -> str | None:
    """Summarize one *chunk* of intermediate segments into a condensed line.

    Contract for the rolling chunked summarizer:

    - **Final replies are never summarized.** Any ``phase == "final"`` assistant
      segment is dropped from the chunk before prompting (it is rendered verbatim
      by the view). A chunk that is *only* a final reply yields ``None``.
    - **Empty-summary guard (latent-bug fix).** A chunk has no prior summary to
      fall back to, so an empty/blank provider response degrades to ``None``
      (the view falls back to Verbose) rather than masquerading as a real
      summary. This is the fix for the reasoning-model empty-output bug where an
      empty string silently became a "successful" summary.
    """

    intermediate = [s for s in segments if not is_final_segment(s)]
    if not intermediate:
        return None

    selected_provider = provider if provider is not None else build_default_summary_provider()
    if selected_provider is None:
        return None
    prompt = build_summary_prompt(
        segments=intermediate,
        state=state,
        prior_condensed=None,
    )
    summary = await selected_provider.summarize(prompt)
    # Empty-summary guard: blank → None (degrade to Verbose), not a fake summary.
    return summary.strip() if isinstance(summary, str) and summary.strip() else None


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


def is_final_segment(segment: Segment) -> bool:
    """True for the verbatim final assistant reply (never summarized)."""
    return (
        str(segment.get("type", "")) == "assistant"
        and str(segment.get("phase", "")) == "final"
    )


def _segment_for_prompt(segment: Segment) -> dict[str, Any]:
    segment_type = str(segment.get("type", ""))
    wanted: tuple[str, ...]
    if segment_type == "assistant":
        wanted = ("type", "phase", "text", "elapsed")
    elif segment_type == "tool_call":
        # Descriptor only — strip input/result payloads to a compact string.
        return {"type": "tool_call", "descriptor": tool_call_descriptor(segment)}
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
    "is_final_segment",
    "summarize_chunk",
    "summarize_doc",
    "summarize_segments",
    "summarize_transcript",
    "tool_call_descriptor",
]
