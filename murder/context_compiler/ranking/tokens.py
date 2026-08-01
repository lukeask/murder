"""Narrow token-count protocol wrapping ``murder.codebase_map.tokens``.

Kept behind a protocol so a real tokenizer can replace the crude estimate
without touching ranking call sites.
"""

from __future__ import annotations

from typing import Protocol

from murder.codebase_map.tokens import count_tokens as _count_tokens


class TokenCounter(Protocol):
    def count_tokens(self, text: str) -> int: ...


class ApproxTokenCounter:
    """Default: ``len // 4`` via codebase_map."""

    def count_tokens(self, text: str) -> int:
        return _count_tokens(text)


DEFAULT_TOKEN_COUNTER = ApproxTokenCounter()


__all__ = [
    "ApproxTokenCounter",
    "DEFAULT_TOKEN_COUNTER",
    "TokenCounter",
]
