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


# Reasoning models spend completion tokens thinking before any content comes
# out, so the provider ``max_tokens`` cap must include headroom beyond the
# content budget or small-budget calls starve to empty output (observed live:
# gpt-oss-120b returns completion_tokens == cap with empty text at caps of
# 128-256). Content budgets are still enforced by prompt + local measurement.
REASONING_HEADROOM = 1024


__all__ = ["count_tokens", "REASONING_HEADROOM"]
