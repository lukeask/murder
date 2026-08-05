"""Commands for the consolidated per-user database."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated

import typer

from murder.state.persistence.backup import backup_database
from murder.state.persistence.connection import connect, RepoDb
from murder.state.persistence.harness_control import prune_harness_capture_retention
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

    The current checkout and any explicitly listed repository paths are merged.
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


@db_app.command("prune-capture")
def cmd_db_prune_capture(
    backup: Annotated[
        bool,
        typer.Option("--backup/--no-backup", help="Back up murder.db before pruning."),
    ] = True,
    vacuum: Annotated[
        bool,
        typer.Option("--vacuum", help="Run VACUUM after pruning (slow; shrinks file on disk)."),
    ] = False,
    raw_minutes: Annotated[
        int,
        typer.Option("--raw-minutes", help="Retain raw terminal frames newer than this."),
    ] = 10,
    observation_hours: Annotated[
        int,
        typer.Option("--observation-hours", help="Observation revision window (hours)."),
    ] = 1,
) -> None:
    """Prune harness capture tables to shrink oversized shared databases.

    Creates a backup by default, then deletes stale harness-control frames and
    observation revisions for every registered repository partition.
    """
    path = db_path()
    if not path.exists():
        typer.secho("No shared murder.db exists.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    if backup:
        result = backup_database(path)
        typer.secho(f"Backup written to {result}", fg=typer.colors.GREEN)
    conn = connect(path)
    try:
        init_db(conn)
        repository_ids = [
            str(row["repository_id"])
            for row in conn.execute("SELECT repository_id FROM repositories").fetchall()
        ]
        if not repository_ids:
            typer.echo("No repository partitions registered.")
            return
        now = datetime.now(timezone.utc)
        raw_retention = timedelta(minutes=raw_minutes)
        observation_retention = timedelta(hours=observation_hours)
        total_observations = 0
        total_frames = 0
        for repository_id in repository_ids:
            db = RepoDb(conn=conn, repository_id=repository_id)
            partition_observations = 0
            partition_frames = 0
            while True:
                observations, frames = prune_harness_capture_retention(
                    db,
                    raw_frame_retention=raw_retention,
                    observation_retention=observation_retention,
                    now=now,
                    max_batches=100,
                )
                partition_observations += observations
                partition_frames += frames
                if observations == 0 and frames == 0:
                    break
            total_observations += partition_observations
            total_frames += partition_frames
            typer.echo(
                f"partition {repository_id}: "
                f"{partition_observations} observations, {partition_frames} frames"
            )
        if vacuum:
            typer.echo("Running VACUUM (this may take a while)...")
            conn.execute("VACUUM")
        typer.secho(
            f"Pruned {total_observations} observations and {total_frames} frames total.",
            fg=typer.colors.GREEN,
        )
    finally:
        conn.close()
