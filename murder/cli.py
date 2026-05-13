"""Murder CLI surface (D8)."""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import signal
import sqlite3
import subprocess
from datetime import datetime
from importlib import resources
from pathlib import Path
from typing import Annotated

import typer

from murder import __version__
from murder import db as dbmod
from murder.bus import TicketStatus
from murder.config import Config, HarnessRoleConfig, project_env_path
from murder.harnesses import REGISTRY
from murder.orchestrator import Orchestrator
from murder.plans.sync import PlanSync, content_hash
from murder.runtime import Runtime
from murder.storage.filesystem import read_lock_pid
from murder.storage.paths import agents_dir, db_path, lock_path, plans_dir, ticket_md
from murder.tickets import lifecycle
from murder.tickets import parser as ticket_parser
from murder.tickets import waves as waves_mod
from murder.tickets.schema import ChecklistItem, Ticket

app = typer.Typer(
    name="murder",
    help="Agentic dev harness — a murder of crows.",
    no_args_is_help=False,
    invoke_without_command=True,
    add_completion=False,
)
tickets_app = typer.Typer(help="Create and import tickets.")
app.add_typer(tickets_app, name="ticket")


def _repo_root() -> Path:
    return Path.cwd().resolve()


def _configured_harnesses(role_cfg: HarnessRoleConfig) -> list[str]:
    harnesses = [role_cfg.harness]
    if role_cfg.harnesses:
        harnesses.extend(role_cfg.harnesses)
    return list(dict.fromkeys(harnesses))


def _harness_executable(kind: str, role_cfg: HarnessRoleConfig) -> str:
    if role_cfg.binary and kind == role_cfg.harness:
        return role_cfg.binary
    cmd = REGISTRY[kind]().startup_cmd(Path("."))
    if not cmd:
        raise ValueError("empty startup command")
    return cmd[0]


def _validate_configured_harness_binaries(cfg: Config) -> list[str]:
    issues: list[str] = []
    for role_name, role_cfg in (
        ("collaborator", cfg.collaborator),
        ("default_crow", cfg.default_crow),
    ):
        for kind in _configured_harnesses(role_cfg):
            try:
                exe = _harness_executable(kind, role_cfg)
            except KeyError:
                issues.append(f"{role_name} harness {kind}: unknown harness")
                continue
            except Exception as e:
                issues.append(f"{role_name} harness {kind}: startup command unavailable ({e})")
                continue
            if shutil.which(exe) is None:
                issues.append(f"{role_name} harness {kind}: {exe} not on PATH")
    return issues


def _open_existing_db(repo: Path) -> sqlite3.Connection:
    path = db_path(repo)
    if not path.exists():
        typer.secho("No murder.db — run murder init", err=True)
        raise typer.Exit(1)
    conn = dbmod.connect(path)
    dbmod.init_schema(conn)
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
    if ad.exists() and force:
        shutil.rmtree(ad)
    ad.mkdir(parents=True, exist_ok=True)
    for sub in ("tickets", "plans", "shelved", "escalations", "runs"):
        (ad / sub).mkdir(parents=True, exist_ok=True)

    tpl_root = resources.files("murder.templates")
    project_name = repo.name

    roles_text = tpl_root.joinpath("roles.yaml").read_text(encoding="utf-8")
    quoted_project_name = project_name.replace("'", "''")
    roles_text = roles_text.replace(
        "name: TODO_SET_ME", f"name: '{quoted_project_name}'", 1
    )
    (ad / "roles.yaml").write_text(roles_text, encoding="utf-8")
    (ad / "env.example").write_text(
        tpl_root.joinpath("env.example").read_text(encoding="utf-8"), encoding="utf-8"
    )
    project_env_path(repo).write_text(
        tpl_root.joinpath("env.example").read_text(encoding="utf-8"), encoding="utf-8"
    )
    _append_gitignore_entries(repo, tpl_root.joinpath("gitignore").read_text(encoding="utf-8"))

    conn = dbmod.connect(db_path(repo))
    dbmod.init_schema(conn)
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


def _pid_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _friendly_lock_message(repo: Path) -> str:
    pid = read_lock_pid(lock_path(repo))
    pid_text = f" (PID {pid})" if pid is not None else ""
    return (
        f"murder is already running in this repo{pid_text}.\n"
        "Stop it with `murder down`, or run from inside the running TUI."
    )


def _require_git_head(repo: Path) -> None:
    inside = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        check=False,
        text=True,
    )
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        raise RuntimeError("murder kick requires a git checkout with at least one commit.")
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        check=False,
        text=True,
    )
    if head.returncode != 0:
        raise RuntimeError(
            "git repo has no commits yet; make an initial commit before `murder kick`."
        )


def kick_preflight(cfg: Config, repo: Path) -> None:
    _require_git_head(repo)
    if cfg.project.name == "TODO_SET_ME":
        typer.secho(
            "Warning: project.name is still TODO_SET_ME; open Settings (ctrl+p) in the TUI to set it.",
            fg=typer.colors.YELLOW,
            err=True,
        )


def _run_async_entry(coro) -> None:  # type: ignore[no-untyped-def]
    try:
        asyncio.run(coro)
    except BlockingIOError:
        typer.secho(_friendly_lock_message(_repo_root()), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None
    except RuntimeError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from e


async def _bare_kickoff(ticket: str | None) -> None:
    repo = _repo_root()
    cfg = Config.load(repo)
    kick_preflight(cfg, repo)
    async with Runtime(cfg, repo) as rt:
        orch = Orchestrator(rt)
        kicked = await orch.kickoff_ready(only=ticket)
        typer.echo(f"Kicked off tickets: {', '.join(kicked) if kicked else '(none)'}")
        if kicked:
            typer.echo("Waiting for SIGINT/SIGTERM (CrowHandler poll loop is running).")
            await rt.run_until_signal()


async def _launch_tui() -> None:
    repo = _repo_root()
    cfg = Config.load(repo)
    os.environ.setdefault("GIO_USE_VFS", "local")
    os.environ.setdefault("GSETTINGS_BACKEND", "memory")
    os.environ.setdefault("DBUS_SESSION_BUS_ADDRESS", "disabled:")
    os.environ.setdefault("NO_AT_BRIDGE", "1")
    async with Runtime(cfg, repo) as rt:
        orch = Orchestrator(rt)
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

    _ensure_initialized_for_bare_command(_repo_root())
    _run_async_entry(_launch_tui())
    raise typer.Exit(0)


@app.command("kick")
def cmd_kick(
    ticket: str = typer.Argument(..., help="Ticket id to kick off (e.g. 't007')."),
) -> None:
    """Kick off a single ticket's Crow from the CLI (no TUI)."""
    _run_async_entry(_bare_kickoff(ticket))




@tickets_app.command("create")
def cmd_ticket_create(
    ticket_id: Annotated[str, typer.Argument(help="Ticket id, e.g. t007.")],
    title: Annotated[str, typer.Argument(help="Ticket title.")],
    wave: Annotated[int, typer.Option("--wave", "-w", min=0, help="Ticket wave.")] = 0,
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
    write: Annotated[
        list[Path] | None,
        typer.Option("--write", help="Write-set path. Repeatable."),
    ] = None,
    check: Annotated[
        list[str] | None,
        typer.Option("--check", help="Checklist item. Repeatable."),
    ] = None,
    skill: Annotated[
        list[str] | None,
        typer.Option("--skill", help="Skill name. Repeatable."),
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
        else {"plan": "", "working_notes": "", "sentinel_notes": "", "_preamble": ""}
    )
    if plan is not None:
        sections["plan"] = plan

    now = datetime.utcnow()
    ticket = Ticket(
        id=ticket_id,
        title=title,
        wave=wave,
        status=status,
        write_set=list(write or []),
        deps=list(dep or []),
        skills=list(skill or []),
        harness=harness,
        model=model,
        created_at=now,
        updated_at=now,
        checklist=[
            ChecklistItem(ord=ord_, text=text)
            for ord_, text in enumerate(check or [])
        ],
    )

    conn = _open_existing_db(repo)
    try:
        dbmod.insert_ticket(conn, ticket)
    except Exception as e:
        typer.secho(f"Failed to create ticket {ticket_id}: {e}", err=True)
        raise typer.Exit(1) from e
    finally:
        conn.close()

    ticket_parser.write_ticket_md(md_path, sections)
    typer.echo(f"Created {ticket_id}: {title}")
    typer.echo(f"Markdown: {md_path.relative_to(repo)}")


@app.command("init")
def cmd_init(
    force: bool = typer.Option(False, "--force", help="Overwrite existing .murder/ tree."),
) -> None:
    """Scaffold .murder/ and create murder.db in the current repo."""
    repo = _repo_root()
    ad = _scaffold_project(repo, force=force)
    typer.secho(f"Initialized {ad} and {db_path(repo)}", fg=typer.colors.GREEN)


@app.command("up")
def cmd_up() -> None:
    """Launch the TUI runtime (alias of bare `murder`)."""
    _run_async_entry(_launch_tui())


@app.command("down")
def cmd_down() -> None:
    """Signal a running murder process via `.murder/.lock` pid."""
    repo = _repo_root()
    pid = read_lock_pid(lock_path(repo))
    if pid is None:
        typer.secho("No lock pid found (murder not running?).", err=True)
        raise typer.Exit(1)
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        with contextlib.suppress(FileNotFoundError):
            lock_path(repo).unlink()
        typer.echo(f"Removed stale lock for dead PID {pid}.")
        return
    typer.echo(f"Sent SIGTERM to pid {pid}")


@app.command("doctor")
def cmd_doctor() -> None:
    """Sanity-check environment and config."""

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
            check=False,
            text=True,
        )
        if p.returncode != 0 or p.stdout.strip() != "true":
            issues.append("not a git checkout; `murder kick` requires git diff checks")
        else:
            head = subprocess.run(
                ["git", "-C", str(repo), "rev-parse", "HEAD"],
                capture_output=True,
                check=False,
                text=True,
            )
            if head.returncode != 0:
                issues.append(
                    "git repo has no commits yet; make an initial commit before `murder kick`"
                )
    try:
        cfg = Config.load(repo)
    except Exception as e:
        issues.append(f"config load failed: {e}")
    else:
        if cfg.project.name == "TODO_SET_ME":
            issues.append("project.name is TODO_SET_ME; run `murder config`")
        issues.extend(_validate_configured_harness_binaries(cfg))
    if not os.environ.get("OPENROUTER_API_KEY"):
        issues.append("OPENROUTER_API_KEY unset (CrowHandler/Sentinel need it)")
    if not agents_dir(repo).exists():
        issues.append(".murder/ missing — run murder init")
    elif not db_path(repo).exists():
        issues.append("murder.db missing — run murder init")
    lock = lock_path(repo)
    if lock.exists():
        pid = read_lock_pid(lock)
        if pid is None:
            issues.append(f"lock file exists but has no readable PID: {lock}")
        elif _pid_is_alive(pid):
            issues.append(f"another murder runtime is running here (PID {pid} in {lock})")
        else:
            issues.append(f"stale murder lock for dead PID {pid}: run `murder down`")
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
    # Import/sync plan markdown before lint checks so orphan-plan warnings
    # don't require launching the full runtime first.
    asyncio.run(PlanSync(repo, conn).reconcile_all())
    issues: list[str] = []
    plan_rows = {r["name"]: dict(r) for r in conn.execute("SELECT * FROM plans").fetchall()}
    for name, row in plan_rows.items():
        md = repo / row["materialized_path"]
        if not md.exists():
            issues.append(f"plan {name}: missing markdown {md}")
            continue
        file_hash = content_hash(md.read_text(encoding="utf-8"))
        last_hash = row["last_materialized_hash"]
        if last_hash and row["body_hash"] != last_hash and file_hash != last_hash:
            issues.append(f"plan {name}: DB/file conflict")
        if row["sync_state"] == "parse_error":
            issues.append(f"plan {name}: parse error: {row['parse_error']}")
        elif row["sync_state"] == "conflict":
            issues.append(f"plan {name}: conflict: {row['conflict_reason']}")
    if plans_dir(repo).exists():
        for md in plans_dir(repo).glob("*.md"):
            if md.stem not in plan_rows:
                issues.append(f"plan {md.stem}: orphan markdown {md}")
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
        if trow["status"] == TicketStatus.DONE.value:
            for p in trow.get("write_set") or []:
                pp = (repo / p).resolve()
                if not pp.exists():
                    issues.append(f"ticket {tid}: done ticket write_set path missing: {p}")
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
        raise typer.Exit(1) from e
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
