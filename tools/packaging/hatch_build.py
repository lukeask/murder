"""Hatchling build hook: bundle the Ink TUI and the web frontend into the wheel.

The distributed `murder` wheel ships two generated, **never committed** front-end payloads, both
regenerated from source on every wheel build so staleness is structurally impossible:

* The Ink TUI as a single self-contained JS bundle at ``murder/_inktui/index.js`` (esbuild),
  run by the user's Node at launch.
* The web/mobile React frontend as a static SPA at ``murder/_webui/`` (an ``index.html`` plus
  hashed JS/CSS under ``assets/``), built by Vite and served by ``ApplicationSocketServer``. Asset
  resolution prefers ``webui/dist`` when present, falling back to packaged ``murder/_webui/``.

This hook, during a **wheel** build, runs one root ``npm ci`` for the npm workspace, regenerates the
protocol in the isolated build environment, then builds ``inktui`` and ``webui`` through their workspace scripts. It copies
the output into
``murder/_inktui/`` and force-includes it. It likewise force-includes the browser artifact
directory. Because those destinations are gitignored, hatchling would otherwise drop them from the
VCS-derived file list —
so we register them via ``build_data["force_include"]``.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
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
            if self.target_name == "sdist":
                self._include_workspace_sources(Path(self.root), build_data)
            return
        # Editable/dev installs resolve front-ends from the source checkout at runtime. The Ink
        # entrypoint runs through tsx; the bridge falls back to the web build output.
        if version == "editable":
            return

        repo_root = Path(self.root)
        force_include = build_data.setdefault("force_include", {})
        self._install_workspace(repo_root)
        self._build_inktui(repo_root, force_include)
        self._build_webui(repo_root, force_include)

    def _include_workspace_sources(
        self, repo_root: Path, build_data: dict[str, Any]
    ) -> None:
        """Force workspace source/config/test inputs into a source distribution.

        Hatch's VCS file selection does not discover newly introduced workspace directories until
        they are present in the revision used for a build. Force-including them makes an unpacked
        sdist self-sufficient during the migration and remains correct after the directories are
        committed.
        """
        force_include = build_data.setdefault("force_include", {})
        for workspace in ("ui-core", "webui"):
            workspace_dir = repo_root / workspace
            if not workspace_dir.is_dir():
                raise RuntimeError(
                    f"hatch_build: expected {workspace}/ at {workspace_dir} for the sdist."
                )
            for source in workspace_dir.rglob("*"):
                if not source.is_file() or {"node_modules", "dist"} & set(source.parts):
                    continue
                relative = source.relative_to(repo_root).as_posix()
                force_include[str(source)] = relative

    def _install_workspace(self, repo_root: Path) -> None:
        """Install the complete frontend graph from the workspace root once."""
        self._run(["npm", "ci"], cwd=repo_root)
        self._run([sys.executable, "tools/generate_application_protocol.py"], cwd=repo_root)

    def _build_inktui(self, repo_root: Path, force_include: dict[str, str]) -> None:
        inktui_dir = repo_root / "inktui"
        if not inktui_dir.is_dir():
            raise RuntimeError(
                f"hatch_build: expected inktui/ at {inktui_dir}; cannot build the TUI bundle."
            )

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
