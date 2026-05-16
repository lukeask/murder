"""Optional JSONL timing log for TUI refresh paths (zero cost when disabled).

Enable with ``MURDER_TUI_PERF_LOG=1`` (default path under ``.murder/logs/``) or
``MURDER_TUI_PERF_LOG=/path/to/file.jsonl``. Disabled when unset, empty,
``0``, or ``false`` (case-insensitive).

Fields are documented on ``PerfLog.event`` / ``PerfLog.span``; each JSON line
includes at least ``ts`` (ISO8601 UTC) and ``name``.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Iterator

_LOG_SIZE_ROTATE_BYTES = 10 * 1024 * 1024


def make_perf_log(repo_root: Path) -> PerfLog:
    """Build a ``PerfLog`` from ``MURDER_TUI_PERF_LOG`` (read once at app init)."""
    raw = os.environ.get("MURDER_TUI_PERF_LOG", "").strip()
    if not raw or raw.lower() in ("0", "false"):
        return PerfLog(None)
    if raw == "1" or raw.lower() == "true":
        from murder.storage.paths import logs_dir

        path = logs_dir(repo_root) / "tui_perf.log"
    else:
        path = Path(raw).expanduser()
    return PerfLog(path)


class PerfLog:
    """Append JSONL timing lines when ``enabled``; otherwise no-op."""

    def __init__(self, path: Path | None) -> None:
        self._path = path
        self._fp: Any = None
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > _LOG_SIZE_ROTATE_BYTES:
            rotated = path.with_name(path.name + ".1")
            if rotated.exists():
                rotated.unlink()
            path.rename(rotated)
        self._fp = open(path, "a", buffering=1, encoding="utf-8")

    @property
    def enabled(self) -> bool:
        return self._fp is not None

    def close(self) -> None:
        if self._fp is not None:
            self._fp.close()
            self._fp = None

    def event(self, name: str, **fields: Any) -> None:
        if not self.enabled:
            return
        self._emit(name, fields)

    @contextmanager
    def span(self, name: str, **fields: Any) -> Iterator[dict[str, Any]]:
        if not self.enabled:
            yield {}
            return
        t0 = perf_counter()
        dynamic: dict[str, Any] = {}
        try:
            yield dynamic
        finally:
            dur_ms = round((perf_counter() - t0) * 1000, 3)
            merged = {**fields, **dynamic, "dur_ms": dur_ms}
            self._emit(name, merged)

    def _emit(self, name: str, payload: dict[str, Any]) -> None:
        assert self._fp is not None
        ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        line = json.dumps({"ts": ts, "name": name, **payload}, separators=(",", ":"))
        self._fp.write(line + "\n")
