"""Resolve the packaged or dev-build web UI static asset directory."""

from __future__ import annotations

from pathlib import Path


def resolve_webui_assets_dir(repo_root: Path | None = None) -> Path | None:
    """Return the directory containing ``index.html`` for the web SPA.

    Prefer a dev build at ``{repo_root}/webui/dist`` when present (murder's own
    repo during frontend work). Otherwise fall back to packaged assets shipped in
    wheels at ``murder/_webui/`` so arbitrary project repos still get a UI.
    """
    if repo_root is not None:
        dev = repo_root / "webui" / "dist"
        if dev.is_dir() and (dev / "index.html").is_file():
            return dev
    packaged = Path(__file__).resolve().parents[2] / "_webui"
    if packaged.is_dir() and (packaged / "index.html").is_file():
        return packaged
    return None


__all__ = ["resolve_webui_assets_dir"]
