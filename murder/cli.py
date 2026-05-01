"""Murder CLI surface (D8)."""

from __future__ import annotations

import asyncio
import contextlib
import shutil
import subprocess
from importlib import resources
from pathlib import Path

import typer

from murder import __version__
from murder import db as dbmod
from murder.config import Config
from murder.orchestrator import Orchestrator
from murder.runtime import Runtime
from murder.storage.filesystem import read_lock_pid
from murder.storage.paths import agents_dir, db_path, lock_path
from murder.bus import TicketStatus
from murder.tickets import lifecycle
from murder.tickets import waves as waves_mod
from murder.tickets.schema import ChecklistItem, Ticket

app = typer.Typer(
    name="murder",
    help="Agentic dev harness — a murder of crows over a monkey.",
    no_args_is_help=False,
    invoke_without_command=True,
    add_completion=False,
)


def _repo_root() -> Path:
    return Path.cwd().resolve()


async def _bare_kickoff(ticket: str | None) -> None:
    repo = _repo_root()
    cfg = Config.load(repo)
    async with Runtime(cfg, repo) as rt:
        orch = Orchestrator(rt)
        kicked = await orch.kickoff_ready(only=ticket)
        typer.echo(f"Kicked off tickets: {', '.join(kicked) if kicked else '(none)'}")
        if kicked:
            typer.echo("Waiting for SIGINT/SIGTERM (Augur poll loop is running).")
            await rt.run_until_signal()


async def _launch_tui() -> None:
    repo = _repo_root()
    cfg = Config.load(repo)
    async with Runtime(cfg, repo) as rt:
        orch = Orchestrator(rt)
        import os

        if os.environ.get("OPENROUTER_API_KEY"):
            with contextlib.suppress(Exception):
                await orch.ensure_sentinel()
        from murder.tui.app import MurderApp

        app_ui = MurderApp(rt, orchestrator=orch)
        await app_ui.run_async()


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

    asyncio.run(_launch_tui())
    raise typer.Exit(0)


@app.command("kick")
def cmd_kick(
    ticket: str = typer.Argument(..., help="Ticket id to kick off (e.g. 't007')."),
) -> None:
    """Kick off a single ticket's Monkey from the CLI (no TUI)."""
    asyncio.run(_bare_kickoff(ticket))


@app.command("init")
def cmd_init(
    force: bool = typer.Option(False, "--force", help="Overwrite existing .agents/ tree."),
) -> None:
    """Scaffold .agents/ and create murder.db in the current repo."""
    repo = _repo_root()
    ad = agents_dir(repo)
    if ad.exists() and not force:
        typer.secho(
            f"Refusing: {ad} already exists. Use --force to delete and re-scaffold.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)
    if ad.exists() and force:
        shutil.rmtree(ad)
    ad.mkdir(parents=True, exist_ok=True)
    for sub in ("tickets", "plans", "shelved", "escalations", "runs"):
        (ad / sub).mkdir(parents=True, exist_ok=True)
    tpl_root = resources.files("murder.templates")
    (ad / "roles.yaml").write_text(
        tpl_root.joinpath("roles.yaml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (ad / "env.example").write_text(
        tpl_root.joinpath("env.example").read_text(encoding="utf-8"), encoding="utf-8"
    )
    gi = tpl_root.joinpath("gitignore").read_text(encoding="utf-8")
    root_gitignore = repo / ".gitignore"
    if root_gitignore.exists():
        existing = root_gitignore.read_text(encoding="utf-8")
        to_add = [ln for ln in gi.splitlines() if ln and ln not in existing]
        if to_add:
            root_gitignore.write_text(
                existing.rstrip() + "\n\n# murder\n" + "\n".join(to_add) + "\n",
                encoding="utf-8",
            )
    else:
        root_gitignore.write_text(gi + "\n", encoding="utf-8")
    conn = dbmod.connect(db_path(repo))
    dbmod.init_schema(conn)
    conn.close()
    typer.secho(f"Initialized {ad} and {db_path(repo)}", fg=typer.colors.GREEN)


@app.command("up")
def cmd_up() -> None:
    """Launch the TUI runtime (alias of bare `murder`)."""
    asyncio.run(_launch_tui())


@app.command("down")
def cmd_down() -> None:
    """Signal a running murder process via `.agents/.lock` pid."""
    import os
    import signal

    repo = _repo_root()
    pid = read_lock_pid(lock_path(repo))
    if pid is None:
        typer.secho("No lock pid found (murder not running?).", err=True)
        raise typer.Exit(1)
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        typer.secho(f"Process {pid} not found.", err=True)
        raise typer.Exit(1)
    typer.echo(f"Sent SIGTERM to pid {pid}")


@app.command("doctor")
def cmd_doctor() -> None:
    """Sanity-check environment and config."""
    import os
    import shutil

    repo = _repo_root()
    issues: list[str] = []
    if shutil.which("tmux") is None:
        issues.append("tmux not found on PATH")
    if shutil.which("git") is None:
        issues.append("git not found on PATH")
    else:
        p = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
        )
        if p.returncode != 0 or p.stdout.strip() != "true":
            issues.append("not a git checkout (expected for D5 diff checks)")
    if not os.environ.get("OPENROUTER_API_KEY"):
        issues.append("OPENROUTER_API_KEY unset (Augur/Sentinel need it)")
    for name, exe in (
        ("cursor agent", "agent"),
        ("claude", "claude"),
    ):
        if shutil.which(exe) is None:
            issues.append(f"{name} ({exe}) not on PATH — optional if unused")
    if not agents_dir(repo).exists():
        issues.append(".agents/ missing — run murder init")
    elif not db_path(repo).exists():
        issues.append("murder.db missing — run murder init")
    try:
        Config.load(repo)
    except Exception as e:
        issues.append(f"config load failed: {e}")
    if issues:
        for i in issues:
            typer.secho(f"- {i}", fg=typer.colors.YELLOW, err=True)
        raise typer.Exit(1)
    typer.secho("doctor: OK", fg=typer.colors.GREEN)


@app.command("lint")
def cmd_lint() -> None:
    """Reconcile DB ↔ markdown ↔ filesystem; print mismatches."""
    from datetime import datetime

    repo = _repo_root()
    if not db_path(repo).exists():
        typer.secho("No murder.db — run murder init", err=True)
        raise typer.Exit(1)
    conn = dbmod.connect(db_path(repo))
    issues: list[str] = []
    rows = conn.execute("SELECT id FROM tickets").fetchall()
    tickets: list[Ticket] = []
    for r in rows:
        tid = r["id"]
        md = agents_dir(repo) / "tickets" / f"{tid}.md"
        if not md.exists():
            issues.append(f"ticket {tid}: missing markdown {md}")
        trow = dbmod.get_ticket(conn, tid)
        if not trow:
            continue
        tickets.append(
            Ticket(
                id=trow["id"],
                title=trow["title"],
                wave=trow["wave"],
                status=TicketStatus(trow["status"]),
                harness=trow.get("harness"),
                model=trow.get("model"),
                attempts=trow["attempts"],
                created_at=datetime.fromisoformat(trow["created_at"]),
                updated_at=datetime.fromisoformat(trow["updated_at"]),
                write_set=[Path(p) for p in trow.get("write_set") or []],
                deps=list(trow.get("deps") or []),
                skills=list(trow.get("skills") or []),
                checklist=[
                    ChecklistItem(
                        id=c.get("id"),
                        ord=c["ord"],
                        text=c["text"],
                        done=bool(c["done"]),
                        done_at=datetime.fromisoformat(c["done_at"])
                        if c.get("done_at")
                        else None,
                    )
                    for c in trow.get("checklist") or []
                ],
            )
        )
        for p in trow.get("write_set") or []:
            pp = (repo / p).resolve()
            if not pp.exists():
                issues.append(f"ticket {tid}: write_set path missing: {p}")
    by_wave: dict[int, list[Ticket]] = {}
    for t in tickets:
        by_wave.setdefault(t.wave, []).append(t)
    for w, ts in by_wave.items():
        try:
            waves_mod.topo_partition(ts)
        except waves_mod.CycleError as e:
            issues.append(f"wave {w}: {e}")
        for a, b, overlap in waves_mod.write_set_conflicts(ts):
            issues.append(f"wave {w}: write_set overlap {a}/{b}: {overlap}")
        for tid, dep in waves_mod.misordered_deps(ts):
            issues.append(f"wave {w}: misordered dep {tid} -> {dep}")
    conn.close()
    if issues:
        for i in issues:
            typer.echo(i)
        raise typer.Exit(1)
    typer.echo("lint: OK")


@app.command("reopen")
def cmd_reopen(ticket_id: str) -> None:
    """Mark a done ticket as planned and cascade to dependents (D7)."""
    repo = _repo_root()
    conn = dbmod.connect(db_path(repo))
    try:
        cascaded = lifecycle.reopen(conn, ticket_id)
    except lifecycle.InvalidTransition as e:
        typer.secho(str(e), err=True)
        conn.close()
        raise typer.Exit(1)
    conn.close()
    typer.echo(f"Reopened {ticket_id}; cascaded: {', '.join(cascaded) if cascaded else '(none)'}")


@app.command("replay")
def cmd_replay(run_id: str) -> None:
    """Print events for a past run as a timeline."""
    repo = _repo_root()
    conn = dbmod.connect(db_path(repo))
    rows = conn.execute(
        "SELECT id, ts, type, agent_id, ticket_id, payload_json FROM events "
        "WHERE run_id = ? ORDER BY id",
        (run_id,),
    ).fetchall()
    conn.close()
    if not rows:
        typer.secho(f"No events for run_id={run_id}", err=True)
        raise typer.Exit(1)
    for r in rows:
        typer.echo(
            f"{r['ts']} [{r['type']}] agent={r['agent_id']} ticket={r['ticket_id']} "
            f"payload={r['payload_json'][:200]}"
        )


@app.command("status")
def cmd_status() -> None:
    """Print a concise status snapshot (no TUI)."""
    repo = _repo_root()
    if not db_path(repo).exists():
        typer.echo("No database — murder init")
        return
    conn = dbmod.connect(db_path(repo))
    typer.echo("Tickets by status:")
    for st in ("planned", "ready", "in_progress", "blocked", "done", "failed"):
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM tickets WHERE status = ?", (st,)
        ).fetchone()["c"]
        typer.echo(f"  {st}: {n}")
    typer.echo("Agents:")
    for r in conn.execute(
        "SELECT agent_id, role, ticket_id, status FROM agents "
        "ORDER BY started_at DESC LIMIT 20"
    ).fetchall():
        typer.echo(
            f"  {r['agent_id']} role={r['role']} ticket={r['ticket_id']} status={r['status']}"
        )
    pend = dbmod.list_pending_escalations(conn)
    typer.echo(f"Pending escalations: {len(pend)}")
    conn.close()


if __name__ == "__main__":
    app()
