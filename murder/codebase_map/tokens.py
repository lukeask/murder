"""Canonical budget tokenizer for the codebase map.

This is the *one* definition used everywhere the map does budget math, so
the 15% (file) / 5% (agent) budgets are well-defined across providers.
"""

from __future__ import annotations


def count_tokens(text: str) -> int:
    """Approximate the token count of ``text``.

    Uses the ``len // 4`` rule of thumb. This is deliberately crude and
    documented as such so it can be swapped without touching call sites.

    # TODO: swap for tiktoken
    """

    return len(text) // 4


__all__ = ["count_tokens"]
