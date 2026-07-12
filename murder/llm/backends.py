"""Immediate and batch inference backend protocols (§13.4).

Immediate completion stays on :class:`~murder.llm.clients.base.APIClient`.
Batch-capable providers may additionally implement
:class:`BatchInferenceBackend`. Immediate-only providers must not be forced
to stub meaningless batch methods.
"""

from __future__ import annotations

from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from murder.llm.clients.base import CompletionResult, ToolSpec

InferenceJobStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]


class CompletionRequest(BaseModel):
    """Normalized completion payload shared by immediate and batch backends."""

    model_config = ConfigDict(extra="ignore")

    model: str
    system: str = ""
    messages: list[dict[str, Any]] = Field(default_factory=list)
    tools: list[ToolSpec] | None = None
    max_tokens: int = 1024
    temperature: float = 0.0


class SubmittedInference(BaseModel):
    """Opaque handle returned by :meth:`BatchInferenceBackend.submit`."""

    model_config = ConfigDict(extra="ignore")

    job_id: str
    provider_id: str
    model_id: str
    # Provider-specific resume tokens (batch id, poll URL, etc.).
    extras: dict[str, Any] = Field(default_factory=dict)


class InferenceStatus(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: InferenceJobStatus
    error: str | None = None
    progress: float | None = None


@runtime_checkable
class ImmediateInferenceBackend(Protocol):
    """Job-free completion interface (matches existing APIClient.complete)."""

    async def complete(
        self,
        request: CompletionRequest,
    ) -> CompletionResult: ...


@runtime_checkable
class BatchInferenceBackend(Protocol):
    """Job-oriented interface for batch-capable providers."""

    async def submit(
        self,
        request: CompletionRequest,
    ) -> SubmittedInference: ...

    async def status(
        self,
        submission: SubmittedInference,
    ) -> InferenceStatus: ...

    async def result(
        self,
        submission: SubmittedInference,
    ) -> CompletionResult: ...


def as_immediate_backend(client: Any) -> ImmediateInferenceBackend | None:
    """Adapt an :class:`APIClient` (kwargs ``complete``) into the request protocol.

    Returns ``None`` when *client* has no ``complete`` callable.
    """
    complete = getattr(client, "complete", None)
    if not callable(complete):
        return None

    class _Adapter:
        async def complete(self, request: CompletionRequest) -> CompletionResult:
            return await complete(
                model=request.model,
                system=request.system,
                messages=request.messages,
                tools=request.tools,
                max_tokens=request.max_tokens,
                temperature=request.temperature,
            )

    return _Adapter()


__all__ = [
    "BatchInferenceBackend",
    "CompletionRequest",
    "ImmediateInferenceBackend",
    "InferenceJobStatus",
    "InferenceStatus",
    "SubmittedInference",
    "as_immediate_backend",
]
