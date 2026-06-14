"""CLI entrypoint — assembles the murder command group from the four command modules."""

from __future__ import annotations

from pathlib import Path

import typer

from murder import __version__
from murder.app.cli.doctor_cmd import cmd_doctor
from murder.app.cli.init_cmd import (
    _ensure_initialized_for_bare_command,
    cmd_init,
    tickets_app,
)
from murder.app.cli.service_cmd import (
    _run_async_entry,
    apply_client_advanced_logging,
    apply_client_log_level,
    cmd_down,
    cmd_id,
    cmd_lint,
    cmd_ls,
    cmd_reopen,
    cmd_replay,
    cmd_retry,
    cmd_serviced,
    cmd_status,
)
from murder.app.cli.tui_cmd import _launch_tui, cmd_up

app = typer.Typer(
    name="murder",
    help="Agentic dev harness — a murder of crows.",
    no_args_is_help=False,
    invoke_without_command=True,
    add_completion=False,
)
app.add_typer(tickets_app, name="ticket")


@app.callback()
def _root(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", "-V", help="Print version and exit."),
    log_level: str | None = typer.Option(
        None,
        "--log-level",
        help="Log level: DEBUG, INFO, WARNING, ERROR (default INFO).",
        case_sensitive=False,
    ),
    advanced_logging: bool = typer.Option(
        False,
        "--advanced-logging",
        help="Open the redacted flight-recorder DB under .murder/advlogs/.",
    ),
    advanced_logging_raw: bool = typer.Option(
        False,
        "--advanced-logging-raw",
        help="Flight recorder with UNREDACTED bodies (implies --advanced-logging).",
    ),
) -> None:
    """Bare entrypoint launches the TUI. Kickoff is `/murder` inside the chat pane."""
    if version:
        typer.echo(f"murder {__version__}")
        raise typer.Exit(0)

    # Resolve + propagate the level to the env BEFORE any service subprocess is
    # spawned, and configure this client process. Runs for subcommands too.
    apply_client_log_level(log_level)
    apply_client_advanced_logging(advanced_logging, advanced_logging_raw)

    if ctx.invoked_subcommand is not None:
        return

    _ensure_initialized_for_bare_command(Path.cwd().resolve())
    _run_async_entry(_launch_tui())
    raise typer.Exit(0)


app.command("init")(cmd_init)
app.command("doctor")(cmd_doctor)
app.command("up")(cmd_up)
app.command("serviced", hidden=True)(cmd_serviced)
app.command("down")(cmd_down)
app.command("id")(cmd_id)
app.command("ls")(cmd_ls)
app.command("lint")(cmd_lint)
app.command("reopen")(cmd_reopen)
app.command("retry")(cmd_retry)
app.command("replay")(cmd_replay)
app.command("status")(cmd_status)
