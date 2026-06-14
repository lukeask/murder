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


def logs_dir(repo_root: Path) -> Path:
    """Directory for diagnostic logs (e.g. TUI perf JSONL)."""
    return agents_dir(repo_root) / "logs"


def db_path(repo_root: Path) -> Path:
    return agents_dir(repo_root) / "murder.db"


def lock_path(repo_root: Path) -> Path:
    return agents_dir(repo_root) / ".lock"


def runs_dir(repo_root: Path) -> Path:
    return agents_dir(repo_root) / "runs"


def worktrees_dir(repo_root: Path) -> Path:
    return agents_dir(repo_root) / "worktrees"


def advlogs_dir(repo_root: Path) -> Path:
    """Directory for the opt-in advanced flight-recorder SQLite DBs (Phase 2)."""
    return murder_dir(repo_root) / "advlogs"


def advanced_log_path(repo_root: Path, run_id: str, *, raw: bool, when: str | None = None) -> Path:
    """Per-session advanced-log DB path.

    ``advanced-YYYYMMDD-HHMMSS-<run_id>.db``, or the deliberately-distinct
    ``advanced-RAW-...`` variant when ``raw`` is set so the unredacted artifact
    is impossible to confuse for the redacted one. ``when`` (a pre-formatted
    ``YYYYMMDD-HHMMSS`` stamp) is injectable for tests; otherwise it is computed
    from the wall clock at call time.
    """
    from datetime import datetime

    stamp = when or datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    marker = "RAW-" if raw else ""
    return advlogs_dir(repo_root) / f"advanced-{marker}{stamp}-{run_id}.db"


def run_dir(repo_root: Path, run_id: str) -> Path:
    return runs_dir(repo_root) / run_id


def panes_dir(repo_root: Path, run_id: str) -> Path:
    return run_dir(repo_root, run_id) / "panes"


def service_log(repo_root: Path, run_id: str) -> Path:
    """Structured per-run NDJSON service log (the Phase 1 default-tier stream)."""
    return run_dir(repo_root, run_id) / "service.log"


def tickets_dir(repo_root: Path) -> Path:
    """Flat ticket markdown directory."""
    return agents_dir(repo_root) / "tickets"


def ticket_md(repo_root: Path, ticket_id: str) -> Path:
    return tickets_dir(repo_root) / f"{ticket_id}.md"


def ticket_yaml(repo_root: Path, ticket_id: str) -> Path:
    return tickets_dir(repo_root) / f"{ticket_id}.yaml"


def plans_dir(repo_root: Path) -> Path:
    return agents_dir(repo_root) / "plans"


def deprecated_plans_dir(repo_root: Path) -> Path:
    return plans_dir(repo_root) / "deprecated_plans"


def plan_md(repo_root: Path, name: str) -> Path:
    return plans_dir(repo_root) / f"{name}.md"


def notes_dir(repo_root: Path) -> Path:
    return agents_dir(repo_root) / "notes"


def note_md(repo_root: Path, name: str) -> Path:
    return notes_dir(repo_root) / f"{name}.md"


def reports_dir(repo_root: Path) -> Path:
    return agents_dir(repo_root) / "reports"


def report_md(repo_root: Path, name: str) -> Path:
    return reports_dir(repo_root) / f"{name}.md"


def escalations_dir(repo_root: Path) -> Path:
    return agents_dir(repo_root) / "escalations"


def escalation_md(repo_root: Path, escalation_id: int) -> Path:
    return escalations_dir(repo_root) / f"{escalation_id}.md"


def shelved_dir(repo_root: Path) -> Path:
    return agents_dir(repo_root) / "shelved"


def roles_yaml(repo_root: Path) -> Path:
    return agents_dir(repo_root) / "roles.yaml"


def harnesses_and_models_md(repo_root: Path) -> Path:
    """Generated doc listing enabled harnesses, models, and effort levels."""
    return agents_dir(repo_root) / "HARNESSES_AND_MODELS.md"


def crow_context_dir(repo_root: Path) -> Path:
    """Directory for project-level context documents injected into crow briefs."""
    return murder_dir(repo_root) / "context"


def notetaker_context_md(repo_root: Path) -> Path:
    """Singleton markdown backing the notetaker prompt context (DB is source of truth)."""
    return agents_dir(repo_root) / "notetakercontext.md"


def tui_prefs_path(repo_root: Path) -> Path:
    return murder_dir(repo_root) / "tui_prefs.json"
