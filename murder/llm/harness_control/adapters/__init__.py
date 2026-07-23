"""Adapter protocols. Concrete harness adapters belong at this edge."""

from murder.llm.harness_control.adapters.acp import AcpHarnessAdapter
from murder.llm.harness_control.adapters.antigravity import AntigravityHarnessAdapter
from murder.llm.harness_control.adapters.base import HarnessActionAdapter, HarnessObservationAdapter
from murder.llm.harness_control.adapters.claude_agent_sdk import ClaudeAgentSdkHarnessAdapter
from murder.llm.harness_control.adapters.claude_code import ClaudeCodeAdapter
from murder.llm.harness_control.adapters.codex import CodexHarnessAdapter
from murder.llm.harness_control.adapters.codex_app_server import CodexAppServerHarnessAdapter
from murder.llm.harness_control.adapters.cursor import CursorHarnessAdapter
from murder.llm.harness_control.adapters.cursor_acp import CursorAcpHarnessAdapter
from murder.llm.harness_control.adapters.pi import PiHarnessAdapter

__all__ = [
    "AcpHarnessAdapter",
    "AntigravityHarnessAdapter",
    "ClaudeAgentSdkHarnessAdapter",
    "ClaudeCodeAdapter",
    "CodexAppServerHarnessAdapter",
    "CodexHarnessAdapter",
    "CursorAcpHarnessAdapter",
    "CursorHarnessAdapter",
    "HarnessActionAdapter",
    "HarnessObservationAdapter",
    "PiHarnessAdapter",
]
