"""Hatchling build hook: bundle the Ink TUI into the wheel.

The distributed `murder` wheel ships the Ink TUI as a single self-contained JS bundle at
`murder/_inktui/index.js`, run by the user's Node at launch (see the build/release strategy in
plan ``newui-finalpush6``). The bundle is **never committed** (``murder/_inktui/`` is gitignored);
it is regenerated from ``inktui/src`` on every wheel build, so staleness is structurally impossible.

This hook, during a **wheel** build, runs ``npm ci && npm run bundle`` in ``inktui/`` (esbuild →
one self-contained ``dist/bundle/index.js``), copies the output into ``murder/_inktui/``, and
force-includes it in the wheel. Because the destination is gitignored, hatchling would otherwise
drop it from the VCS-derived file list — so we register it via ``build_data["force_include"]``.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class InkTuiBundleHook(BuildHookInterface):
    """Build the Ink TUI bundle and ride it along in the wheel."""

    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        # Only the wheel ships the prebuilt bundle. Skip for sdist (which carries source only) and
        # any other target, so we don't shell out to npm needlessly.
        if self.target_name != "wheel":
            return

        repo_root = Path(self.root)
        inktui_dir = repo_root / "inktui"
        if not inktui_dir.is_dir():
            raise RuntimeError(
                f"hatch_build: expected inktui/ at {inktui_dir}; cannot build the TUI bundle."
            )

        # Install deps deterministically, then bundle. `npm ci` requires a lockfile (committed) and
        # is reproducible; if a clean environment lacks one it should fail loudly, not silently skip
        # the bundle (a wheel without the bundle is broken).
        self._run(["npm", "ci"], cwd=inktui_dir)
        self._run(["npm", "run", "bundle"], cwd=inktui_dir)

        bundle_dir = inktui_dir / "dist" / "bundle"
        index_js = bundle_dir / "index.js"
        if not index_js.is_file():
            raise RuntimeError(
                f"hatch_build: esbuild did not produce {index_js}; the bundle is missing."
            )

        dest_dir = repo_root / "murder" / "_inktui"
        dest_dir.mkdir(parents=True, exist_ok=True)
        # Copy every bundle output (index.js, plus any sidecar such as a .wasm if the toolchain ever
        # emits one) so the packaged set always matches what esbuild produced.
        force_include = build_data.setdefault("force_include", {})
        for artifact in sorted(bundle_dir.iterdir()):
            if not artifact.is_file():
                continue
            dest = dest_dir / artifact.name
            shutil.copy2(artifact, dest)
            # Gitignored generated file → force it into the wheel under the murder package.
            force_include[str(dest)] = f"murder/_inktui/{artifact.name}"

    def _run(self, cmd: list[str], *, cwd: Path) -> None:
        try:
            subprocess.run(cmd, cwd=str(cwd), check=True)
        except FileNotFoundError as exc:  # npm/node absent on the build machine
            raise RuntimeError(
                f"hatch_build: `{cmd[0]}` not found. Building the murder wheel needs Node ≥ 20 and "
                "npm on the build machine (the CI release runner provides these)."
            ) from exc
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"hatch_build: `{' '.join(cmd)}` failed (exit {exc.returncode}) in {cwd}."
            ) from exc
