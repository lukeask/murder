"""OpenRouter HTTP client (OAI-compatible).

Retries with exponential backoff on 5xx / network errors.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

import httpx

from murder.clients.base import APIClient, CompletionResult, ToolCall, ToolSpec

OPENROUTER_BASE = "https://openrouter.ai/api/v1"


class OpenRouterClient(APIClient):
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = OPENROUTER_BASE,
        http_referer: str = "https://github.com/lukeask/murder",
        app_title: str = "murder",
    ) -> None:
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
        if not self.api_key:
            raise RuntimeError("OPENROUTER_API_KEY is unset")
        self.base_url = base_url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": http_referer,
            "X-Title": app_title,
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
    ) -> CompletionResult:
        client = await self._ensure_client()
        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "system", "content": system}, *messages],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]
            payload["tool_choice"] = "auto"

        url = f"{self.base_url}/chat/completions"
        last_exc: Exception | None = None
        backoff = 1.0
        for attempt in range(3):
            t0 = time.monotonic()
            try:
                resp = await client.post(url, json=payload)
                if resp.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        f"server error {resp.status_code}",
                        request=resp.request,
                        response=resp,
                    )
                resp.raise_for_status()
                data = resp.json()
                latency_ms = (time.monotonic() - t0) * 1000
                return _parse_completion(data, model, latency_ms)
            except (httpx.HTTPStatusError, httpx.RequestError) as e:
                last_exc = e
                if attempt == 2:
                    break
                await asyncio.sleep(backoff)
                backoff *= 2
        assert last_exc is not None
        raise last_exc

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


def _parse_completion(data: dict[str, Any], model: str, latency_ms: float) -> CompletionResult:
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError(f"no choices in completion response: {data}")
    msg = choices[0].get("message") or {}
    text = msg.get("content")
    raw_tool_calls = msg.get("tool_calls") or []
    tool_calls: list[ToolCall] = []
    import json

    for tc in raw_tool_calls:
        fn = tc.get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {"_raw": fn.get("arguments")}
        tool_calls.append(
            ToolCall(
                name=fn.get("name", ""),
                arguments=args,
                call_id=tc.get("id", ""),
            )
        )
    usage = data.get("usage") or {}
    return CompletionResult(
        text=text,
        tool_calls=tool_calls,
        prompt_tokens=int(usage.get("prompt_tokens", 0)),
        completion_tokens=int(usage.get("completion_tokens", 0)),
        model=model,
        latency_ms=latency_ms,
    )
