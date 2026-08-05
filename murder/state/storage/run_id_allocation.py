"""Run-id allocation + per-run dir setup.

A run is one repository-host activation (one ``murder up`` / host start).
Run id format: ``<repository_id>-<unix-ts>`` with the timestamp zero-padded
to 10 chars. On collision within the same repo, append ``_<counter>``.

The ``repository_id`` prefix keeps ``runs.run_id`` (still a global PK in the
shared murder.db) unique across concurrent hosts that would otherwise claim
the same wall-clock timestamp from independent per-repo filesystem claims.
"""

from __future__ import annotations

import time
from pathlib import Path

from murder.state.storage.paths import panes_dir, run_dir, runs_dir


def allocate_run_id(repo_root: Path, *, repository_id: str) -> str:
    """Return an unused run id. Create the per-run directory tree.

    ``repository_id`` is required so two hosts activating different repos in
    the same second cannot collide on the shared ``runs.run_id`` primary key.
    """
    if not repository_id:
        raise ValueError("repository_id is required for run_id allocation")
    runs_dir(repo_root).mkdir(parents=True, exist_ok=True)
    base = f"{repository_id}-{int(time.time()):010d}"
    suffix = 0
    # Creating the run dir with exist_ok=False is the atomic claim: a racing
    # process can create the same dir between an existence check and the mkdir,
    # so retry the next suffix on FileExistsError rather than crashing.
    while True:
        candidate = base if suffix == 0 else f"{base}_{suffix}"
        try:
            run_dir(repo_root, candidate).mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            suffix += 1
            continue
        panes_dir(repo_root, candidate).mkdir(parents=True, exist_ok=False)
        return candidate


def open_pane_log(repo_root: Path, run_id: str, session: str) -> Path:
    """Create (if needed) and return the path to a session's pane logfile."""
    p = panes_dir(repo_root, run_id) / f"{session}.log"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.touch(exist_ok=True)
    return p
