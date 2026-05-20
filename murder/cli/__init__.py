"""CLI entrypoint — assembles the murder command group from the four command modules."""

from __future__ import annotations

from pathlib import Path

import typer

from murder import __version__
from murder.cli.doctor_cmd import cmd_doctor
from murder.cli.init_cmd import (
    _ensure_initialized_for_bare_command,
    cmd_init,
    tickets_app,
)
from murder.cli.service_cmd import (
    _run_async_entry,
    cmd_down,
    cmd_kick,
    cmd_lint,
    cmd_replay,
    cmd_reopen,
    cmd_retry,
    cmd_serviced,
    cmd_status,
)
from murder.cli.tui_cmd import _launch_tui, cmd_up

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
) -> None:
    """Bare entrypoint launches the TUI. Kickoff is `/murder` inside the chat pane."""
    if version:
        typer.echo(f"murder {__version__}")
        raise typer.Exit(0)

    if ctx.invoked_subcommand is not None:
        return

    _ensure_initialized_for_bare_command(Path.cwd().resolve())
    _run_async_entry(_launch_tui())
    raise typer.Exit(0)


app.command("init")(cmd_init)
app.command("kick")(cmd_kick)
app.command("up")(cmd_up)
app.command("serviced", hidden=True)(cmd_serviced)
app.command("down")(cmd_down)
app.command("doctor")(cmd_doctor)
app.command("lint")(cmd_lint)
app.command("reopen")(cmd_reopen)
app.command("retry")(cmd_retry)
app.command("replay")(cmd_replay)
app.command("status")(cmd_status)
