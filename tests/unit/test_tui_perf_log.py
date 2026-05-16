"""TUI perf JSONL logger — disabled path is a no-op; enabled writes one line per span."""

from __future__ import annotations

import json
from pathlib import Path

from murder.tui.perf_log import PerfLog


def test_perf_log_disabled_no_file_write(tmp_path: Path) -> None:
    logf = tmp_path / "nope.log"
    p = PerfLog(None)
    assert not p.enabled
    with p.span("x.should_not_run"):
        pass
    p.event("x.event")
    p.close()
    assert not logf.exists()


def test_perf_log_span_writes_jsonl(tmp_path: Path) -> None:
    logf = tmp_path / "p.log"
    p = PerfLog(logf)
    assert p.enabled
    with p.span("demo.span", ticket="t1") as d:
        d["extra"] = 2
    p.close()
    raw = logf.read_text(encoding="utf-8").strip()
    row = json.loads(raw)
    assert row["name"] == "demo.span"
    assert row["ticket"] == "t1"
    assert row["extra"] == 2
    assert "dur_ms" in row and isinstance(row["dur_ms"], (int, float))
    assert row["ts"].endswith("Z")
