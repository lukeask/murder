"""Honest capability descriptors for verified session transports."""

from __future__ import annotations

from murder.runtime.sessions.contracts import SessionCapabilities

_VERIFIED_TMUX_HARNESSES = frozenset({"antigravity", "claude_code", "codex", "cursor", "pi"})
_STRUCTURED_APPROVAL_HARNESSES = frozenset({"antigravity", "claude_code", "codex"})


def verified_tmux_capabilities(harness: str) -> SessionCapabilities:
    """Describe only semantic operations implemented by a verified adapter.

    A tmux transport can still expose structured controller operations: the
    verified adapter lowers those typed operations to terminal effects.  That
    is distinct from claiming an app-server transport.  In particular, Cursor
    and Pi do not implement the structured question/permission answer actions.
    """

    normalized = harness.strip().casefold()
    if normalized not in _VERIFIED_TMUX_HARNESSES:
        raise ValueError(f"no verified tmux capability descriptor for {harness!r}")
    return SessionCapabilities(
        structured_messages=True,
        structured_tool_events=True,
        structured_approvals=normalized in _STRUCTURED_APPROVAL_HARNESSES,
        raw_terminal=True,
        model_switching=True,
        resumable=True,
        interruptible=True,
        supports_subagents=False,
    )


__all__ = ["verified_tmux_capabilities"]
