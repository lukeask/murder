"""Adapter protocols. Concrete harness adapters belong at this edge."""

from murder.llm.harness_control.adapters.antigravity import AntigravityHarnessAdapter
from murder.llm.harness_control.adapters.base import HarnessActionAdapter, HarnessObservationAdapter
from murder.llm.harness_control.adapters.claude_code import ClaudeCodeAdapter
from murder.llm.harness_control.adapters.codex import CodexHarnessAdapter
from murder.llm.harness_control.adapters.cursor import CursorHarnessAdapter
from murder.llm.harness_control.adapters.pi import PiHarnessAdapter

__all__ = [
    "AntigravityHarnessAdapter",
    "ClaudeCodeAdapter",
    "CodexHarnessAdapter",
    "CursorHarnessAdapter",
    "HarnessActionAdapter",
    "HarnessObservationAdapter",
    "PiHarnessAdapter",
]
