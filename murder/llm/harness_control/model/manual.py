"""Explicit, auditable user-directed terminal input.

This is deliberately distinct from harness semantic actions such as prompt
submission and permission approval. It represents an operator asking Murder
to emit raw input; it does *not* assert what the harness will do with it and
is never eligible for automatic replay.
"""

from __future__ import annotations

from dataclasses import dataclass

from murder.llm.harness_control.model.actions import DuplicatePolicy, SemanticAction


@dataclass(frozen=True, slots=True)
class ManualTerminalInput(SemanticAction):
    """A human-authorized raw terminal input request."""

    text: str
    literal: bool
    append_enter: bool
    source: str = "operator"

    def __post_init__(self) -> None:
        if not self.text:
            raise ValueError("manual terminal input must not be empty")
        if self.duplicate_policy is not DuplicatePolicy.NEVER_AUTOMATICALLY_REPLAY:
            raise ValueError("manual terminal input must never be automatically replayed")


__all__ = ["ManualTerminalInput"]
