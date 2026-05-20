"""Native coding crow adapter (D14).

Placeholder for v2 native agents that run in tmux for uniformity but
speak structured output directly to the bus. v0 keeps the slot reserved
so v2 drops in without restructuring; methods raise.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from murder.harnesses.base import HarnessAdapter


class NativeCodingCrowAdapter(HarnessAdapter):
    kind: ClassVar[str] = "native_coding_crow"
    crow_system_prompt: ClassVar[str] = ""  # native agents prompted directly

    def startup_cmd(self, cwd: Path) -> list[str]:
        # TODO(v2): launch the native agent process; possibly `python -m murder.native ...`.
        raise NotImplementedError("v2: native_coding_crow startup_cmd")

    def is_ready(self, pane_text: str) -> bool:
        # Native agents will signal readiness via the bus, not the pane.
        raise NotImplementedError("v2: native_coding_crow is_ready (bus side-channel)")

    def is_idle(self, pane_text: str) -> bool:
        raise NotImplementedError("v2: native_coding_crow is_idle (bus side-channel)")

    def is_busy(self, pane_text: str) -> bool:
        raise NotImplementedError("v2: native_coding_crow is_busy (bus side-channel)")

    def extract_last_message(self, pane_text: str) -> str | None:
        raise NotImplementedError("v2: native_coding_crow extract_last_message")

    def format_nudge(self, msg: str) -> str:
        return msg  # native agents accept structured input; no framing needed.
