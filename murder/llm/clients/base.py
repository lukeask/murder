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
