from __future__ import annotations

from murder.app.cli.init_cmd import _scaffold_project


def test_scaffold_project_creates_reports_dir(repo_root) -> None:
    _scaffold_project(repo_root)

    assert (repo_root / ".murder" / "reports").is_dir()
