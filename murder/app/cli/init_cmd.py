"""Init and ticket-management commands."""

from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime
from importlib import resources
from pathlib import Path
from typing import Annotated
from uuid import uuid4

import typer

from murder.bus import TicketStatus
from murder.config import project_env_path
from murder.state.persistence.schema import get_db, init_db
from murder.state.persistence.tickets import insert_ticket
from murder.state.storage.paths import agents_dir, db_path, ticket_md
from murder.work.examples import seed_examples
from murder.work.tickets import parser as ticket_parser
from murder.work.tickets.schema import ChecklistItem, Ticket

tickets_app = typer.Typer(help="Create and import tickets.")


def _repo_root() -> Path:
    return Path.cwd().resolve()


def _open_existing_db(repo: Path) -> sqlite3.Connection:
    path = db_path(repo)
    if not path.exists():
        typer.secho("No murder.db — run murder init", err=True)
        raise typer.Exit(1)
    conn = get_db(path)
    init_db(conn)
    return conn


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


def _scaffold_project(repo: Path, *, force: bool = False) -> Path:
    ad = agents_dir(repo)
    if ad.exists() and not force:
        typer.secho(
            f"Refusing: {ad} already exists. Use --force to delete and re-scaffold.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)
    # Read every template up front, BEFORE the destructive rmtree, so a
    # missing/unreadable template resource fails fast and leaves the user's
    # existing .murder/ intact rather than deleting it and then discovering we
    # can't rebuild (init --force footgun).
    tpl_root = resources.files("murder.resources.templates")
    project_name = repo.name
    quoted_project_name = project_name.replace("'", "''")
    roles_text = tpl_root.joinpath("roles.yaml").read_text(encoding="utf-8")
    roles_text = roles_text.replace("name: TODO_SET_ME", f"name: '{quoted_project_name}'", 1)
    env_example_text = tpl_root.joinpath("env.example").read_text(encoding="utf-8")
    gitignore_text = tpl_root.joinpath("gitignore").read_text(encoding="utf-8")

    if ad.exists() and force:
        shutil.rmtree(ad)
    ad.mkdir(parents=True, exist_ok=True)
    for sub in ("tickets", "plans", "reports", "shelved", "escalations", "runs"):
        (ad / sub).mkdir(parents=True, exist_ok=True)

    (ad / "roles.yaml").write_text(roles_text, encoding="utf-8")
    (ad / "env.example").write_text(env_example_text, encoding="utf-8")
    project_env_path(repo).write_text(env_example_text, encoding="utf-8")
    _append_gitignore_entries(repo, gitignore_text)

    seed_examples(repo)

    conn = get_db(db_path(repo))
    init_db(conn)
    conn.close()
    return ad


def _ensure_initialized_for_bare_command(repo: Path) -> None:
    if db_path(repo).exists():
        return
    if agents_dir(repo).exists():
        typer.secho(
            "Found .murder/ but no murder.db. Run `murder init --force` to re-scaffold.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)
    should_init = typer.confirm(
        "This directory has not been initialized for murder. Run `murder init` now?",
        default=True,
    )
    if not should_init:
        typer.secho("Aborted. Run `murder init` when you're ready.", err=True)
        raise typer.Exit(1)
    ad = _scaffold_project(repo)
    typer.secho(f"Initialized {ad} and {db_path(repo)}", fg=typer.colors.GREEN)


def cmd_init(
    force: bool = typer.Option(False, "--force", help="Overwrite existing .murder/ tree."),
) -> None:
    """Scaffold .murder/ and create murder.db in the current repo."""
    repo = _repo_root()
    ad = _scaffold_project(repo, force=force)
    typer.secho(f"Initialized {ad} and {db_path(repo)}", fg=typer.colors.GREEN)


@tickets_app.command("create")
def cmd_ticket_create(
    title: Annotated[str, typer.Argument(help="Ticket title.")],
    ticket_id: Annotated[
        str | None, typer.Option("--id", help="Ticket id (UUID auto-generated if omitted).")
    ] = None,
    status: Annotated[
        TicketStatus,
        typer.Option("--status", help="Initial ticket status."),
    ] = TicketStatus.PLANNED,
    from_file: Annotated[
        Path | None,
        typer.Option(
            "--from",
            "-f",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            help="Markdown file to import for ticket prose sections.",
        ),
    ] = None,
    plan: Annotated[
        str | None,
        typer.Option("--plan", help="Plan body text. Overrides imported ## Plan."),
    ] = None,
    dep: Annotated[
        list[str] | None,
        typer.Option("--dep", help="Dependency ticket id. Repeatable."),
    ] = None,
    check: Annotated[
        list[str] | None,
        typer.Option("--check", help="Checklist item. Repeatable."),
    ] = None,
    harness: Annotated[
        str | None,
        typer.Option("--harness", help="Harness override for this ticket."),
    ] = None,
    model: Annotated[
        str | None,
        typer.Option("--model", help="Model override for this ticket."),
    ] = None,
    overwrite_markdown: Annotated[
        bool,
        typer.Option("--overwrite-markdown", help="Replace an existing ticket markdown file."),
    ] = False,
) -> None:
    """Create/import a ticket row and materialize `.murder/tickets/<id>.md`."""
    if ticket_id is None:
        ticket_id = str(uuid4())
    repo = _repo_root()
    md_path = ticket_md(repo, ticket_id)
    if md_path.exists() and not overwrite_markdown:
        typer.secho(
            f"Refusing: {md_path} already exists. Use --overwrite-markdown to replace it.",
            err=True,
        )
        raise typer.Exit(1)

    sections = (
        ticket_parser.read_ticket_md(from_file)
        if from_file is not None
        else {"plan": "", "working_notes": "", "_preamble": ""}
    )
    if plan is not None:
        sections["plan"] = plan

    now = datetime.utcnow()
    ticket = Ticket(
        id=ticket_id,
        title=title,
        status=status,
        deps=list(dep or []),
        skills=[],
        harness=harness,
        model=model,
        created_at=now,
        updated_at=now,
        checklist=[ChecklistItem(ord=ord_, text=text) for ord_, text in enumerate(check or [])],
    )

    conn = _open_existing_db(repo)
    try:
        insert_ticket(conn, ticket)
    except Exception as e:
        typer.secho(f"Failed to create ticket {ticket_id}: {e}", err=True)
        raise typer.Exit(1) from e
    finally:
        conn.close()

    ticket_parser.write_ticket_md(md_path, sections)
    typer.echo(f"Created {ticket_id}: {title}")
    typer.echo(f"Markdown: {md_path.relative_to(repo)}")
