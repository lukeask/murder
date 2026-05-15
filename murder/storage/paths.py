"""Canonical path helpers.

Centralizes every filesystem location murder cares about so refactors of
the layout (D9, future) touch one place.
"""

from __future__ import annotations

from pathlib import Path

MURDER_DIR_NAME = ".murder"


def murder_dir(repo_root: Path) -> Path:
    return repo_root / MURDER_DIR_NAME


def agents_dir(repo_root: Path) -> Path:
    """Backward-compatible name for the project-local murder state directory."""
    return murder_dir(repo_root)


def db_path(repo_root: Path) -> Path:
    return agents_dir(repo_root) / "murder.db"


def lock_path(repo_root: Path) -> Path:
    return agents_dir(repo_root) / ".lock"


def runs_dir(repo_root: Path) -> Path:
    return agents_dir(repo_root) / "runs"


def run_dir(repo_root: Path, run_id: str) -> Path:
    return runs_dir(repo_root) / run_id


def panes_dir(repo_root: Path, run_id: str) -> Path:
    return run_dir(repo_root, run_id) / "panes"


def tickets_dir(repo_root: Path) -> Path:
    """Flat per D9 — wave is in DB."""
    return agents_dir(repo_root) / "tickets"


def ticket_md(repo_root: Path, ticket_id: str) -> Path:
    return tickets_dir(repo_root) / f"{ticket_id}.md"


def plans_dir(repo_root: Path) -> Path:
    return agents_dir(repo_root) / "plans"


def plan_md(repo_root: Path, name: str) -> Path:
    return plans_dir(repo_root) / f"{name}.md"


def notes_dir(repo_root: Path) -> Path:
    return agents_dir(repo_root) / "notes"


def note_md(repo_root: Path, name: str) -> Path:
    return notes_dir(repo_root) / f"{name}.md"


def escalations_dir(repo_root: Path) -> Path:
    return agents_dir(repo_root) / "escalations"


def escalation_md(repo_root: Path, escalation_id: int) -> Path:
    return escalations_dir(repo_root) / f"{escalation_id}.md"


def shelved_dir(repo_root: Path) -> Path:
    return agents_dir(repo_root) / "shelved"


def roles_yaml(repo_root: Path) -> Path:
    return agents_dir(repo_root) / "roles.yaml"


def notetaker_context_md(repo_root: Path) -> Path:
    """Singleton markdown backing the notetaker prompt context (DB is source of truth)."""
    return agents_dir(repo_root) / "notetakercontext.md"
