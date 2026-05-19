from __future__ import annotations

from pathlib import Path

from murder.harnesses.base import HarnessAdapter


def assert_adapter_basics(adapter: HarnessAdapter, pane: str, cwd: Path) -> None:
    cmd = adapter.startup_cmd(cwd)
    assert isinstance(cmd, list)
    assert cmd
    assert all(isinstance(part, str) for part in cmd)

    assert isinstance(adapter.is_ready(pane), bool)
    assert isinstance(adapter.is_idle(pane), bool)
    assert isinstance(adapter.is_busy(pane), bool)

    last = adapter.extract_last_message(pane)
    assert last is None or isinstance(last, str)
    assert isinstance(adapter.format_nudge("nudge"), str)
