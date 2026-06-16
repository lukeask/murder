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
from murder.app.cli.web_cmd import web_app

app = typer.Typer(
    name="murder",
    help="Agentic dev harness — a murder of crows.",
    no_args_is_help=False,
    invoke_without_command=True,
    add_completion=False,
)
app.add_typer(tickets_app, name="ticket")
app.add_typer(web_app, name="web")


@app.callback()
def _root(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", "-V", help="Print version and exit."),
    log_level: str | None = typer.Option(
        None,
        "--log-level",
        help=(
            "Verbosity ladder (one knob): error, warning, info (default), debug, "
            "advanced (flight recorder, redacted), advanced-raw (unredacted)."
        ),
        case_sensitive=False,
    ),
) -> None:
    """Bare entrypoint launches the TUI. Start a plan with the new-plan popup
    (`alt+p`), which creates a plan and starts its planning agent."""
    if version:
        typer.echo(f"murder {__version__}")
        raise typer.Exit(0)

    # Resolve + propagate the level rung to the env BEFORE any service subprocess
    # is spawned, and configure this client process. Runs for subcommands too.
    # The recorder mode rides the same rung — no separate flag to forward.
    apply_client_log_level(log_level)

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
