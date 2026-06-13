"""Anthropic Messages API client."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any

import httpx

from murder.llm.clients._retry import is_retryable_exc, retry_after_seconds
from murder.llm.clients.base import APIClient, CompletionResult, ToolCall, ToolSpec

ANTHROPIC_BASE = "https://api.anthropic.com/v1"
ANTHROPIC_VERSION = "2023-06-01"
MAX_ATTEMPTS = 3

LOGGER = logging.getLogger(__name__)


class AnthropicClient(APIClient):
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = ANTHROPIC_BASE,
    ) -> None:
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not self.api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is unset")
        self.base_url = base_url.rstrip("/")
        self._headers = {
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "Content-Type": "application/json",
        }
        self._client: httpx.AsyncClient | None = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=60.0, headers=self._headers)
        return self._client

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
        del kwargs  # accepted for wrapper-client forwarding; not used here
        client = await self._ensure_client()
        payload: dict[str, Any] = {
            "model": model,
            "system": system,
            "messages": _to_anthropic_messages(messages),
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            payload["tools"] = [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.parameters,
                }
                for t in tools
            ]

        url = f"{self.base_url}/messages"
        last_exc: Exception | None = None
        backoff = 1.0
        for attempt in range(MAX_ATTEMPTS):
            t0 = time.monotonic()
            try:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                data = resp.json()
                latency_ms = (time.monotonic() - t0) * 1000
                return _parse_completion(data, model, latency_ms)
            except (httpx.HTTPStatusError, httpx.RequestError) as e:
                last_exc = e
                # Terminal 4xx (400/401/413/…) will never succeed on retry.
                if not is_retryable_exc(e):
                    raise
                if attempt == MAX_ATTEMPTS - 1:
                    break
                wait = retry_after_seconds(e)
                if wait is None:
                    wait = backoff
                    backoff *= 2
                else:
                    LOGGER.warning("anthropic rate-limited; honoring Retry-After=%.1fs", wait)
                await asyncio.sleep(wait)
        assert last_exc is not None
        raise last_exc

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


def _to_anthropic_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role")
        if role == "tool":
            converted.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": msg.get("tool_call_id", ""),
                            "content": str(msg.get("content", "")),
                        }
                    ],
                }
            )
            continue
        if role not in ("user", "assistant"):
            LOGGER.warning("dropping message with unsupported role %r from anthropic history", role)
            continue
        content: Any = msg.get("content") or ""
        tool_calls = msg.get("tool_calls") or []
        if tool_calls:
            blocks: list[dict[str, Any]] = []
            if content:
                blocks.append({"type": "text", "text": str(content)})
            for tc in tool_calls:
                fn = tc.get("function") or {}
                args = fn.get("arguments") or "{}"
                try:
                    parsed_args = json.loads(args) if isinstance(args, str) else args
                except json.JSONDecodeError:
                    parsed_args = {"_raw": args}
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": tc.get("id", ""),
                        "name": fn.get("name", ""),
                        "input": parsed_args,
                    }
                )
            content = blocks
        converted.append({"role": role, "content": content})
    return converted


def _parse_completion(data: dict[str, Any], model: str, latency_ms: float) -> CompletionResult:
    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    for block in data.get("content") or []:
        if block.get("type") == "text":
            text_parts.append(str(block.get("text", "")))
        elif block.get("type") == "tool_use":
            tool_calls.append(
                ToolCall(
                    name=str(block.get("name", "")),
                    arguments=dict(block.get("input") or {}),
                    call_id=str(block.get("id", "")),
                )
            )
    usage = data.get("usage") or {}
    if not usage:
        LOGGER.warning(
            "anthropic response missing usage block; cost summary will under-report (model=%s)",
            model,
        )
    return CompletionResult(
        text="\n".join(p for p in text_parts if p) or None,
        tool_calls=tool_calls,
        prompt_tokens=int(usage.get("input_tokens", 0)),
        completion_tokens=int(usage.get("output_tokens", 0)),
        model=str(data.get("model") or model),
        latency_ms=latency_ms,
    )
