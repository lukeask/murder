"""Run-id allocation + per-run dir setup.

A run is one `murder up` (or one bare-`murder` kickoff). Run id format:
`<unix-ts>` zero-padded to 10 chars; on collision, append `_<counter>`.
"""

from __future__ import annotations

import time
from pathlib import Path

from murder.state.storage.paths import panes_dir, run_dir, runs_dir


def allocate_run_id(repo_root: Path) -> str:
    """Return an unused run id; create the per-run directory tree."""
    runs_dir(repo_root).mkdir(parents=True, exist_ok=True)
    base = f"{int(time.time()):010d}"
    candidate = base
    suffix = 0
    while run_dir(repo_root, candidate).exists():
        suffix += 1
        candidate = f"{base}_{suffix}"
    panes_dir(repo_root, candidate).mkdir(parents=True, exist_ok=False)
    return candidate


def open_pane_log(repo_root: Path, run_id: str, session: str) -> Path:
    """Create (if needed) and return the path to a session's pane logfile."""
    p = panes_dir(repo_root, run_id) / f"{session}.log"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.touch(exist_ok=True)
    return p
