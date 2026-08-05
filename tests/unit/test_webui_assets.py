"""resolve_webui_assets_dir — packaged vs dev-build web SPA roots."""

from __future__ import annotations

from pathlib import Path

import murder.app.service.webui_assets as webui_assets
from murder.app.service.webui_assets import resolve_webui_assets_dir


def test_resolve_prefers_repo_webui_dist(tmp_path: Path) -> None:
    dist = tmp_path / "webui" / "dist"
    dist.mkdir(parents=True)
    dist.joinpath("index.html").write_text("<html>dev</html>", encoding="utf-8")

    resolved = resolve_webui_assets_dir(tmp_path)
    assert resolved == dist


def test_resolve_falls_back_to_packaged_webui() -> None:
    packaged = resolve_webui_assets_dir(Path("/nonexistent"))
    assert packaged is not None
    assert packaged.name == "_webui"
    assert (packaged / "index.html").is_file()


def test_resolve_returns_none_when_no_packaged_or_dev_build(
    monkeypatch,
    tmp_path: Path,
) -> None:
    fake_module = tmp_path / "murder" / "app" / "service" / "webui_assets.py"
    fake_module.parent.mkdir(parents=True)
    fake_module.write_text("", encoding="utf-8")
    monkeypatch.setattr(webui_assets, "__file__", str(fake_module))

    assert resolve_webui_assets_dir(tmp_path / "no-webui") is None

