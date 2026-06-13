"""Shared CLI utilities used by multiple command modules."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path


def repo_root() -> Path:
    """Return the resolved current working directory as the project root."""
    return Path.cwd().resolve()


def node_major_version() -> int | None:
    """Return node's major version, or None if node is absent/unusable/unparseable.

    Single source of truth for the node preflight: ``doctor`` and the TUI launch
    path must agree on what counts as a usable node, otherwise "doctor says OK
    but launch fails." Treats a missing binary, a non-zero exit, an OSError, or
    unparseable output all as None.
    """
    if shutil.which("node") is None:
        return None
    try:
        out = subprocess.run(
            ["node", "--version"],
            capture_output=True,
            check=False,
            text=True,
        )
    except OSError:
        return None
    if out.returncode != 0:
        return None
    m = re.match(r"\s*v?(\d+)", out.stdout)
    if not m:
        return None
    return int(m.group(1))


def pid_is_alive(pid: int) -> bool:
    """Whether ``pid`` names a live process (treats EPERM as alive)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
