"""APIClient ABC.

Tools are passed via OpenAI-compatible function-call schema. The client
returns a normalized response (text + optional tool calls) that callers
unpack. Streaming is not used in v0 — CrowHandler's calls are tiny and Sentinel
benefits little from streaming for tool-use loops.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel

from murder.observability.advanced_log import ApiRecord, current_advanced_log


class ToolSpec(BaseModel):
    """OAI-compatible tool spec."""

    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema


class ToolCall(BaseModel):
    name: str
    arguments: dict[str, Any]
    call_id: str


class CompletionResult(BaseModel):
    text: str | None
    tool_calls: list[ToolCall] = []
    # token accounting for cost summary (M7 / D-cost)
    prompt_tokens: int
    completion_tokens: int
    model: str
    latency_ms: float


class APIClient(ABC):
    """Provider-agnostic completion + tool-use client."""

    @abstractmethod
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
        """One-shot completion (no streaming).

        ``**kwargs`` is accepted (and ignored by concrete clients) so wrapper
        clients (AutoFree / model-pinned) can forward caller-supplied extras
        without a signature mismatch crashing the failover path.
        """


def build_request_summary(
    *,
    model: str,
    system: str,
    messages: list[dict[str, Any]],
    tools: list[ToolSpec] | None = None,
    max_tokens: int = 1024,
    temperature: float = 0.0,
) -> dict[str, Any]:
    """Normalize the inbound ``complete()`` args into a recordable request body.

    Full bodies are the point — system + messages + tool schemas all flow
    through verbatim; the advanced-log writer handles redaction. Concrete
    clients call this at the ``complete()`` entry to capture the request once,
    provider-independent (before each provider reshapes it for the wire).
    """
    return {
        "model": model,
        "system": system,
        "messages": messages,
        "tools": [t.model_dump() for t in tools] if tools else None,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }


def record_completion(
    *,
    request_summary: dict[str, Any],
    result: CompletionResult,
    status: str = "ok",
    retries: int | None = None,
) -> None:
    """Flight-recorder seam for the ``complete()`` RETURN boundary (boundary #1).

    Reusable across every concrete :class:`APIClient`: pass the request summary
    built by :func:`build_request_summary` plus the normalized
    :class:`CompletionResult`. Aggregates request + response (text, tool calls,
    token usage, latency, model, status) into a single ``api_records`` row.

    Unconditional by contract: ``current_advanced_log()`` returns a zero-cost
    no-op writer when advanced logging is off. Correlation ids + redaction +
    async enqueue all live inside the writer, so this never blocks or raises
    into the completion path.
    """
    usage = {
        "prompt_tokens": result.prompt_tokens,
        "completion_tokens": result.completion_tokens,
    }
    response = {
        "text": result.text,
        "tool_calls": [tc.model_dump() for tc in result.tool_calls],
        "model": result.model,
        "latency_ms": result.latency_ms,
        **usage,
    }
    current_advanced_log().record_api(
        ApiRecord(
            request=request_summary,
            response=response,
            model=result.model,
            status=status,
            retries=retries,
            usage=usage,
        )
    )
