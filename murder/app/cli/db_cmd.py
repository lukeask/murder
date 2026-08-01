"""Commands for the consolidated per-user database."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from murder.state.persistence.backup import backup_database
from murder.state.persistence.connection import connect
from murder.state.persistence.legacy_merge import merge_known_legacy_databases
from murder.state.persistence.repositories import forget_repository, registered_repository_id
from murder.state.persistence.schema import init_db
from murder.state.storage.paths import db_path, repository_id_path

db_app = typer.Typer(help="Back up and manage repository partitions.")


@db_app.command("backup")
def cmd_db_backup(
    out: Annotated[Path | None, typer.Option("--out", help="Destination database path.")] = None,
) -> None:
    """Create a consistent timestamped backup of the shared database."""
    try:
        result = backup_database(db_path(), out)
    except FileNotFoundError:
        typer.secho("No shared murder.db exists.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None
    except FileExistsError as error:
        typer.secho(f"Refusing to overwrite {error.args[0]}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None
    typer.secho(f"Backup written to {result}", fg=typer.colors.GREEN)


@db_app.command("migrate")
def cmd_db_migrate(
    repos: Annotated[
        list[Path] | None,
        typer.Argument(help="Additional repository checkouts whose legacy databases to merge."),
    ] = None,
) -> None:
    """Merge legacy per-repository databases into the shared database.

    The current checkout and known service-session repositories are always
    considered.  Positional paths cover inactive checkouts that are not in the
    service registry.
    """
    conn = connect()
    try:
        init_db(conn)
        merged = merge_known_legacy_databases(
            conn,
            Path.cwd().resolve(),
            explicit_repos=repos or (),
        )
    finally:
        conn.close()
    if merged:
        for repo in merged:
            typer.secho(f"Migrated {repo}", fg=typer.colors.GREEN)
    else:
        typer.echo("No legacy databases required migration.")


@db_app.command("forget")
def cmd_db_forget(
    repo: Annotated[Path, typer.Argument(help="Repository checkout whose partition to remove.")],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip the confirmation prompt.")] = False,
) -> None:
    """Permanently remove one repository partition from the shared database."""
    path = db_path()
    if not path.exists():
        typer.secho("No shared murder.db exists.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    conn = connect(path)
    try:
        init_db(conn)
        repository_id = registered_repository_id(conn, repo)
        if repository_id is None:
            typer.secho(f"No registered repository partition for {repo.resolve()}", err=True)
            raise typer.Exit(1)
        if not yes and not typer.confirm(
            f"Delete all murder database state for {repo.resolve()} ({repository_id})?",
            default=False,
        ):
            raise typer.Abort()
        forget_repository(conn, repository_id)
    finally:
        conn.close()
    try:
        repository_id_path(repo).unlink()
    except FileNotFoundError:
        pass
    typer.secho(f"Forgot repository partition {repository_id}", fg=typer.colors.GREEN)
