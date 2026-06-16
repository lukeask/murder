"""Filesystem helpers, path conventions, run id allocation.

Most state is in SQLite (D2); this package handles things that have to
live as files: pane logfiles, escalation .md bodies, plan .md files,
ticket prose .md files, lockfile.
"""

from murder.state.storage.paths import agents_dir, db_path, lock_path, runs_dir
from murder.state.storage.run_id_allocation import allocate_run_id

__all__ = ["agents_dir", "db_path", "lock_path", "runs_dir", "allocate_run_id"]
