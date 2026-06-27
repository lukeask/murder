"""Print an Ink pane fixture at an explicit terminal allocation.

Thin Python wrapper around ``inktui/fixtures/print-pane-fixture.ts``. The
canonical pane fixture registry lives under ``inktui/fixtures/``; pane agents
wire new components there as they land in ``inktui/src/components/panes/``.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
INKTUI_DIR = REPO_ROOT / "inktui"
RENDERER = "fixtures/print-pane-fixture.ts"


def render_pane_fixture(
    pane_type: str,
    fixture_data: str,
    lh_allocation: int | str,
    cw_allocation: int | str,
) -> None:
    """Print the ANSI-rich pane fixture for ``pane_type`` and ``fixture_data``.

    Positional arguments match the requested tool contract:
    ``pane_type``, ``fixture_data``, ``lh_allocation``, ``cw_allocation``.
    ``fixture_data`` is the registered data id for the selected pane fixture.
    """

    env = os.environ.copy()
    env["FORCE_COLOR"] = "3"
    env.pop("NO_COLOR", None)
    command = [
        "node",
        "--import",
        "tsx",
        RENDERER,
        pane_type,
        fixture_data,
        str(lh_allocation),
        str(cw_allocation),
    ]
    result = subprocess.run(
        command,
        cwd=INKTUI_DIR,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        if result.stderr:
            sys.stderr.write(result.stderr)
        raise RuntimeError(f"pane fixture renderer exited with status {result.returncode}")
    sys.stdout.write(result.stdout)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Print one pane fixture at an explicit lh/cw allocation.",
    )
    parser.add_argument("pane_type")
    parser.add_argument("fixture_data")
    parser.add_argument("lh_allocation")
    parser.add_argument("cw_allocation")
    args = parser.parse_args(argv)
    try:
        render_pane_fixture(
            args.pane_type,
            args.fixture_data,
            args.lh_allocation,
            args.cw_allocation,
        )
    except RuntimeError:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
