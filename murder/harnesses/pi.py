"""Pi CLI adapter — STUB.

User has not yet exercised pi; auto-yolo behavior unconfirmed. Adapter
raises on use until pi is investigated. Reserved slot in registry so
downstream code can plumb `harness: pi` ticket overrides through without
breaking on import.

TODO(post-M1): exercise `pi --help` and pane behavior; pin regexes; lift
NotImplementedError on `is_ready` etc.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from murder.harnesses.base import HarnessAdapter


class PiAdapter(HarnessAdapter):
    kind: ClassVar[str] = "pi"
    monkey_system_prompt: ClassVar[str] = "see prompts/monkey_pi.md"

    def startup_cmd(self, cwd: Path) -> list[str]:
        return ["pi"]

    def is_ready(self, pane_text: str) -> bool:
        raise NotImplementedError("pi adapter: not yet investigated")

    def is_idle(self, pane_text: str) -> bool:
        raise NotImplementedError("pi adapter: not yet investigated")

    def is_busy(self, pane_text: str) -> bool:
        raise NotImplementedError("pi adapter: not yet investigated")

    def extract_last_message(self, pane_text: str) -> str | None:
        raise NotImplementedError("pi adapter: not yet investigated")

    def format_nudge(self, msg: str) -> str:
        return f"[supervisor] {msg}"
