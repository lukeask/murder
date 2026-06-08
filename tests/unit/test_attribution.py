from __future__ import annotations

from pathlib import Path

from murder.state.storage.paths import (
    deprecated_plans_dir,
    plan_md,
    ticket_md,
    tickets_dir,
)
from murder.work.attribution import attribute_edit


def test_ticket_path_attributes_to_crow(repo_root: Path) -> None:
    path = ticket_md(repo_root, "t042")
    assert attribute_edit(path, repo_root=repo_root) == "crow-t042"


def test_slug_ticket_id_preserved(repo_root: Path) -> None:
    path = ticket_md(repo_root, "T01-scaffold")
    assert attribute_edit(path, repo_root=repo_root) == "crow-T01-scaffold"


def test_plan_path_attributes_to_planner(repo_root: Path) -> None:
    path = plan_md(repo_root, "newui-service")
    assert attribute_edit(path, repo_root=repo_root) == "planner-newui-service"


def test_unrelated_path_is_unattributable(repo_root: Path) -> None:
    assert attribute_edit(repo_root / "README.md", repo_root=repo_root) is None
    assert attribute_edit(repo_root / ".murder" / "notes" / "x.md", repo_root=repo_root) is None


def test_deprecated_plans_subdir_is_not_a_planner(repo_root: Path) -> None:
    # A nested artifact under plans/deprecated_plans/ has no live planner owner.
    path = deprecated_plans_dir(repo_root) / "old.md"
    assert attribute_edit(path, repo_root=repo_root) is None


def test_non_markdown_in_tickets_dir_is_unattributable(repo_root: Path) -> None:
    assert attribute_edit(tickets_dir(repo_root) / "t001.yaml", repo_root=repo_root) is None


def test_accepts_str_path(repo_root: Path) -> None:
    path = str(ticket_md(repo_root, "t007"))
    assert attribute_edit(path, repo_root=str(repo_root)) == "crow-t007"
