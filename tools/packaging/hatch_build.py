"""Hatchling build hook: bundle the Ink TUI and the web frontend into the wheel.

The distributed `murder` wheel ships two generated, **never committed** front-end payloads, both
regenerated from source on every wheel build so staleness is structurally impossible:

* The Ink TUI as a single self-contained JS bundle at ``murder/_inktui/index.js`` (esbuild),
  run by the user's Node at launch.
* The web/mobile React frontend as a static SPA at ``murder/_webui/`` (an ``index.html`` plus
  hashed JS/CSS under ``assets/``), built by Vite and served by ``murder/web/bridge.py``. The
  bridge resolves assets at ``murder/_webui/`` first, falling back to ``webui/dist`` in a source
  checkout.

This hook, during a **wheel** build, runs ``npm ci`` + the build for each front-end in its own
dir (``inktui/`` → ``npm run bundle``; ``webui/`` → ``npm run build``), copies the output into
``murder/_inktui/`` / ``murder/_webui/`` respectively, and force-includes both. Because the
destinations are gitignored, hatchling would otherwise drop them from the VCS-derived file list —
so we register them via ``build_data["force_include"]``.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class InkTuiBundleHook(BuildHookInterface):
    """Build the Ink TUI bundle + the web frontend and ride them along in the wheel."""

    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        # Only the wheel ships the prebuilt front-ends. Skip for sdist (which carries source only)
        # and any other target, so we don't shell out to npm needlessly.
        if self.target_name != "wheel":
            return
        # Editable/dev installs resolve front-ends from the source checkout at runtime
        # (inktui/src via tsx; webui/dist via bridge fallback) — no npm build here.
        if version == "editable":
            return

        repo_root = Path(self.root)
        force_include = build_data.setdefault("force_include", {})
        self._build_inktui(repo_root, force_include)
        self._build_webui(repo_root, force_include)

    def _build_inktui(self, repo_root: Path, force_include: dict[str, str]) -> None:
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
        # Start from a clean dir so a removed artifact can never linger in the wheel.
        if dest_dir.exists():
            shutil.rmtree(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        # Copy every bundle output (index.js, plus any sidecar such as a .wasm if the toolchain ever
        # emits one) so the packaged set always matches what esbuild produced.
        for artifact in sorted(bundle_dir.iterdir()):
            if not artifact.is_file():
                continue
            dest = dest_dir / artifact.name
            shutil.copy2(artifact, dest)
            # Gitignored generated file → force it into the wheel under the murder package.
            force_include[str(dest)] = f"murder/_inktui/{artifact.name}"

    def _build_webui(self, repo_root: Path, force_include: dict[str, str]) -> None:
        webui_dir = repo_root / "webui"
        if not webui_dir.is_dir():
            raise RuntimeError(
                f"hatch_build: expected webui/ at {webui_dir}; cannot build the web frontend."
            )

        # `npm ci` is reproducible and requires the committed lockfile; the web build imports the
        # portable core from ../inktui/src via the `@core` alias (resolved at build time by Vite),
        # so the sdist must also carry inktui/src — see pyproject sdist includes.
        self._run(["npm", "ci"], cwd=webui_dir)
        self._run(["npm", "run", "build"], cwd=webui_dir)

        dist_dir = webui_dir / "dist"
        index_html = dist_dir / "index.html"
        if not index_html.is_file():
            raise RuntimeError(
                f"hatch_build: vite did not produce {index_html}; the web frontend is missing."
            )

        dest_dir = repo_root / "murder" / "_webui"
        # Clean before copying so stale hashed assets from a previous build can't linger.
        if dest_dir.exists():
            shutil.rmtree(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        # Copy the whole dist tree (index.html + assets/ + any other emitted files). The bridge
        # serves this dir verbatim, so the packaged layout must mirror webui/dist exactly.
        for src in sorted(dist_dir.rglob("*")):
            if not src.is_file():
                continue
            rel = src.relative_to(dist_dir)
            dest = dest_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            # Gitignored generated tree → force it into the wheel under the murder package.
            force_include[str(dest)] = f"murder/_webui/{rel.as_posix()}"

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
