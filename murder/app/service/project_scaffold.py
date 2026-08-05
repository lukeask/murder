"""Project scaffold shared by CLI init and daemon/web initialize flows."""

from __future__ import annotations

import re
import shutil
from importlib import resources
from pathlib import Path

from murder.config import project_env_path
from murder.state.persistence.connection import connect, open_repo_db
from murder.state.persistence.repositories import forget_repository, registered_repository_id
from murder.state.persistence.schema import init_db
from murder.state.storage.paths import agents_dir, db_path
from murder.work.examples import seed_examples


class ProjectAlreadyInitialized(Exception):
    """Raised when ``scaffold_project`` refuses to overwrite an existing tree."""

    def __init__(self, agents_path: Path) -> None:
        self.agents_path = agents_path
        super().__init__(
            f"Refusing: {agents_path} already exists. "
            "Use --force to reset its database partition and re-scaffold."
        )


def _append_gitignore_entries(repo: Path, entries: str) -> None:
    root_gitignore = repo / ".gitignore"
    if root_gitignore.exists():
        existing = root_gitignore.read_text(encoding="utf-8")
        to_add = [ln for ln in entries.splitlines() if ln and ln not in existing]
        if to_add:
            root_gitignore.write_text(
                existing.rstrip() + "\n\n# murder\n" + "\n".join(to_add) + "\n",
                encoding="utf-8",
            )
        return
    root_gitignore.write_text(entries.rstrip() + "\n", encoding="utf-8")


_SELECTION_KEY_RE = re.compile(
    r"^(\s+)(harness|harnesses|startup_model|startup_effort|startup_models|"
    r"startup_models_by_harness):"
)
_SELECTION_ROLES = ("collaborator", "planner", "default_crow")


def _strip_selection_fields_from_roles_text(text: str) -> str:
    """Drop harness/model selection lines from the scaffolded roles.yaml.

    Selection is user-scope only (settings menu / ~/.config/murder/config.yaml);
    leaving the bundled defaults in the project file would be dead, confusing keys.
    """
    out: list[str] = []
    current_block: str | None = None
    skip_deeper_than: int | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if line and not line[0].isspace() and stripped.endswith(":"):
            current_block = stripped[:-1]
            skip_deeper_than = None
        indent = len(line) - len(line.lstrip())
        if skip_deeper_than is not None:
            if stripped and indent > skip_deeper_than:
                continue
            skip_deeper_than = None
        if current_block in _SELECTION_ROLES:
            m = _SELECTION_KEY_RE.match(line)
            if m:
                skip_deeper_than = len(m.group(1))
                continue
        out.append(line)
    return "\n".join(out) + "\n"


def scaffold_project(repo: Path, *, force: bool = False) -> Path:
    """Scaffold ``.murder/`` and register a shared-db partition for ``repo``.

    Shared by the CLI ``murder init`` path and daemon/web initialize flows.
    Raises ``ProjectAlreadyInitialized`` when the tree exists and ``force`` is
    false (callers that speak Typer convert this to ``typer.Exit``).
    """
    ad = agents_dir(repo)
    if ad.exists() and not force:
        raise ProjectAlreadyInitialized(ad)
    # Read templates before writing any scaffold files so a missing resource
    # cannot leave a partially refreshed project state.
    tpl_root = resources.files("murder.resources.templates")
    project_name = repo.name
    quoted_project_name = project_name.replace("'", "''")
    roles_text = tpl_root.joinpath("roles.yaml").read_text(encoding="utf-8")
    roles_text = roles_text.replace("name: TODO_SET_ME", f"name: '{quoted_project_name}'", 1)
    roles_text = _strip_selection_fields_from_roles_text(roles_text)
    env_example_text = tpl_root.joinpath("env.example").read_text(encoding="utf-8")
    gitignore_text = tpl_root.joinpath("gitignore").read_text(encoding="utf-8")

    if ad.exists() and force:
        shared_path = db_path()
        if shared_path.exists():
            shared = connect(shared_path)
            try:
                init_db(shared)
                repository_id = registered_repository_id(shared, repo)
                if repository_id is not None:
                    forget_repository(shared, repository_id)
            finally:
                shared.close()
        # Preserve the historical --force contract (full local re-scaffold)
        # while also resetting the now-shared database partition.
        shutil.rmtree(ad)
    ad.mkdir(parents=True, exist_ok=True)
    for sub in ("tickets", "plans", "reports", "shelved", "escalations", "runs"):
        (ad / sub).mkdir(parents=True, exist_ok=True)

    (ad / "roles.yaml").write_text(roles_text, encoding="utf-8")
    (ad / "env.example").write_text(env_example_text, encoding="utf-8")
    project_env_path(repo).write_text(env_example_text, encoding="utf-8")
    _append_gitignore_entries(repo, gitignore_text)

    seed_examples(repo)

    open_repo_db(repo).close()

    from murder.user_config import ensure_user_themes

    ensure_user_themes()
    return ad


__all__ = ["ProjectAlreadyInitialized", "scaffold_project"]
