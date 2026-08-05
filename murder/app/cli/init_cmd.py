"""Init and ticket-management commands."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Annotated
from uuid import uuid4

import typer

from murder.app.service.project_scaffold import (
    ProjectAlreadyInitialized,
    scaffold_project,
)
from murder.state.persistence.connection import RepoDb, open_repo_db
from murder.state.persistence.tickets import insert_ticket
from murder.state.storage.paths import agents_dir, db_path, repository_id_path, ticket_md
from murder.work.tickets import parser as ticket_parser
from murder.work.tickets.schema import ChecklistItem, Ticket
from murder.work.tickets.status import TicketStatus

tickets_app = typer.Typer(help="Create and import tickets.")

# Back-compat alias for older imports / smoke tests.
_scaffold_project = scaffold_project

__all__ = [
    "ProjectAlreadyInitialized",
    "_scaffold_project",
    "cmd_init",
    "scaffold_project",
    "tickets_app",
]


def _repo_root() -> Path:
    return Path.cwd().resolve()


def _open_existing_db(repo: Path) -> RepoDb:
    if not agents_dir(repo).exists():
        typer.secho("No murder.db — run murder init", err=True)
        raise typer.Exit(1)
    return open_repo_db(repo)


def _ensure_initialized_for_bare_command(repo: Path) -> None:
    if repository_id_path(repo).exists():
        return
    if agents_dir(repo).exists():
        open_repo_db(repo).close()
        return
    should_init = typer.confirm(
        "This directory has not been initialized for murder. Run `murder init` now?",
        default=True,
    )
    if not should_init:
        typer.secho("Aborted. Run `murder init` when you're ready.", err=True)
        raise typer.Exit(1)
    try:
        ad = scaffold_project(repo)
    except ProjectAlreadyInitialized as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc
    typer.secho(f"Initialized {ad} with partition in {db_path()}", fg=typer.colors.GREEN)


def cmd_init(
    force: bool = typer.Option(
        False,
        "--force",
        help="Reset this repository's database partition and refresh its scaffold.",
    ),
) -> None:
    """Scaffold .murder/ and create murder.db in the current repo."""
    repo = _repo_root()
    try:
        ad = scaffold_project(repo, force=force)
    except ProjectAlreadyInitialized as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc
    typer.secho(f"Initialized {ad} with partition in {db_path()}", fg=typer.colors.GREEN)


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
