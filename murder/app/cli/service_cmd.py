"""Service lifecycle and ticket-operation commands."""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import typer

from murder.bus import TicketStatus
from murder.bus.client import SocketBusClient
from murder.bus.protocol import ClientKind
from murder.bus.transport_socket import default_socket_path
from murder.config import Config
from murder.state.persistence.escalations import list_pending_escalations
from murder.state.persistence.schema import get_db, init_db
from murder.state.persistence.tickets import get_ticket
from murder.work.plans.sync import PlanSync, content_hash
from murder.app.service.host import ServiceHost
from murder.state.storage.filesystem import read_lock_pid
from murder.state.storage.paths import (
    agents_dir,
    db_path,
    lock_path,
    logs_dir,
    notes_dir,
    plans_dir,
)
from murder.state.storage.service_registry import (
    AmbiguousServiceSessionError,
    ServiceSession,
    list_service_sessions,
    project_session_name,
    remove_service_session,
    resolve_service_session_selector,
)
from murder.work.tickets import lifecycle
from murder.work.tickets.schema import ChecklistItem, Ticket
from murder.work.tickets.sync import TicketSync
from murder.app.cli._util import repo_root as _repo_root


def _open_existing_db(repo: Path):  # type: ignore[return]
    path = db_path(repo)
    if not path.exists():
        typer.secho("No murder.db — run murder init", err=True)
        raise typer.Exit(1)
    conn = get_db(path)
    init_db(conn)
    return conn


def _pid_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _live_service_sessions() -> list[ServiceSession]:
    sessions: list[ServiceSession] = []
    for session in list_service_sessions():
        if _pid_is_alive(session.pid):
            sessions.append(session)
        else:
            remove_service_session(session.name)
    return sessions


async def _socket_is_connectable(socket_path: Path, *, timeout_s: float = 0.5) -> bool:
    try:
        client = SocketBusClient(
            socket_path,
            client_kind=ClientKind.CLI_EPHEMERAL,
            client_id=f"probe-{os.getpid()}",
        )
        await asyncio.wait_for(
            client.request("health.ping", {}, timeout_s=timeout_s),
            timeout=timeout_s + 0.25,
        )
    except Exception:
        return False
    return True


async def _supervisor_is_live(repo: Path, socket_path: Path) -> bool:
    pid = read_lock_pid(lock_path(repo))
    return bool(
        pid is not None and _pid_is_alive(pid) and await _socket_is_connectable(socket_path)
    )


def _spawn_service_process(repo: Path) -> subprocess.Popen[bytes]:
    log_root = logs_dir(repo) / datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    log_root.mkdir(parents=True, exist_ok=True)
    log_file = open(log_root / "supervisor.ndjson", "ab", buffering=0)
    return subprocess.Popen(
        [sys.executable, "-m", "murder", "serviced"],
        cwd=str(repo),
        stdin=subprocess.DEVNULL,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        close_fds=True,
    )


async def _ensure_supervisor(repo: Path, socket_path: Path) -> None:
    if await _supervisor_is_live(repo, socket_path):
        return
    _spawn_service_process(repo)
    delays = (0.25, 0.5, 1.0, 1.0, 1.0, 1.0)
    for delay in delays:
        await asyncio.sleep(delay)
        if await _supervisor_is_live(repo, socket_path):
            return
    raise RuntimeError("supervisor did not become ready within 5s")


async def _ensure_supervisor_started(repo: Path, socket_path: Path) -> bool:
    """Return True when this call started the supervisor, False if it was already live."""
    if await _supervisor_is_live(repo, socket_path):
        return False
    await _ensure_supervisor(repo, socket_path)
    return True


def _friendly_lock_message(repo: Path) -> str:
    pid = read_lock_pid(lock_path(repo))
    pid_text = f" (PID {pid})" if pid is not None else ""
    return (
        f"murder is already running in this repo{pid_text}.\n"
        "Stop it with `murder down`, or run from inside the running TUI."
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


async def _run_supervisor_only(tcp_port: int | None = None) -> None:
    repo = _repo_root()
    cfg = Config.load(repo)
    host = ServiceHost(cfg, repo, socket_path=default_socket_path(repo), tcp_port=tcp_port)
    async with host:
        try:
            await host.run_until_signal()
        finally:
            # A service shutdown is authoritative (`murder down`), unlike the
            # old in-process TUI quit path. Let Runtime stop agents/tmux.
            with contextlib.suppress(Exception):
                if host.runtime is not None:
                    host.runtime._external_stop.clear()


def cmd_serviced(
    tcp_port: int = typer.Option(0, "--tcp-port", help="Also listen on TCP; 0 = disabled."),
) -> None:
    """Internal supervisor-only service entrypoint."""
    _run_async_entry(_run_supervisor_only(tcp_port=tcp_port or None))


def _signal_service(repo: Path, pid: int, *, session_name: str | None = None) -> None:
    if pid is None:
        typer.secho("No lock pid found (murder not running?).", err=True)
        raise typer.Exit(1)
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        with contextlib.suppress(FileNotFoundError):
            lock_path(repo).unlink()
        if session_name is not None:
            remove_service_session(session_name)
        typer.echo(f"Removed stale lock for dead PID {pid}.")
        return
    typer.echo(f"Sent SIGTERM to pid {pid}")


def _down_named_session(selector: str) -> None:
    sessions = _live_service_sessions()
    try:
        session = resolve_service_session_selector(selector, sessions)
    except AmbiguousServiceSessionError as exc:
        typer.secho(
            f"Multiple murder services share basename {exc.selector!r}. Use the full session id:",
            err=True,
        )
        for match in exc.matches:
            typer.secho(
                f"  murder down -s {match.name}  # hash={match.path_hash} repo={match.repo_root}",
                err=True,
            )
        raise typer.Exit(1) from exc

    if session is None:
        typer.secho(f"No murder service session named {selector!r}. Run `murder ls`.", err=True)
        raise typer.Exit(1)
    _signal_service(session.repo_root, session.pid, session_name=session.name)


def cmd_down(
    session: str | None = typer.Option(
        None,
        "--session",
        "-s",
        help="Stop a service by session id or unambiguous directory basename.",
    ),
) -> None:
    """Signal a running murder process."""
    if session:
        _down_named_session(session)
        return

    repo = _repo_root()
    pid = read_lock_pid(lock_path(repo))
    if pid is None:
        typer.secho("No lock pid found (murder not running?).", err=True)
        raise typer.Exit(1)
    _signal_service(repo, pid, session_name=project_session_name(repo))


def cmd_id() -> None:
    """Print the current directory's murder service session id."""
    typer.echo(project_session_name(_repo_root()))


def cmd_ls() -> None:
    """List running murder service instances."""
    sessions = sorted(_live_service_sessions(), key=lambda s: (s.basename, s.name))
    if not sessions:
        typer.echo("No murder services running.")
        return

    typer.echo(f"{'SESSION':<30} {'PID':>7}  REPO")
    for session in sessions:
        typer.echo(f"{session.name:<30} {session.pid:>7}  {session.repo_root}")


def cmd_status() -> None:
    """Print a concise status snapshot (no TUI)."""
    repo = _repo_root()
    if not db_path(repo).exists():
        typer.echo("No database — murder init")
        return
    conn = get_db(db_path(repo))
    typer.echo("Tickets by status:")
    for st in ("planned", "ready", "in_progress", "blocked", "done", "failed"):
        n = conn.execute("SELECT COUNT(*) AS c FROM tickets WHERE status = ?", (st,)).fetchone()[
            "c"
        ]
        typer.echo(f"  {st}: {n}")
    typer.echo("Agents:")
    for r in conn.execute(
        "SELECT agent_id, role, ticket_id, status FROM agents ORDER BY started_at DESC LIMIT 20"
    ).fetchall():
        typer.echo(
            f"  {r['agent_id']} role={r['role']} ticket={r['ticket_id']} status={r['status']}"
        )
    pend = list_pending_escalations(conn)
    typer.echo(f"Pending escalations: {len(pend)}")
    conn.close()


def cmd_reopen(ticket_id: str) -> None:
    """Mark a done ticket as planned and cascade to dependents (D7)."""
    repo = _repo_root()
    conn = get_db(db_path(repo))
    try:
        cascaded = lifecycle.reopen(conn, ticket_id)
    except lifecycle.InvalidTransition as e:
        typer.secho(str(e), err=True)
        conn.close()
        raise typer.Exit(1) from e
    conn.close()
    typer.echo(f"Reopened {ticket_id}; cascaded: {', '.join(cascaded) if cascaded else '(none)'}")


def cmd_retry(ticket_id: str) -> None:
    """Retry a failed ticket — transition failed → planned and clear its last_error."""
    repo = _repo_root()
    conn = get_db(db_path(repo))
    try:
        lifecycle.transition(conn, ticket_id, TicketStatus.PLANNED, reason="retry")
        lifecycle.clear_last_error(conn, ticket_id)
    except lifecycle.InvalidTransition as e:
        typer.secho(str(e), err=True)
        conn.close()
        raise typer.Exit(1) from e
    conn.close()
    typer.echo(f"Retried {ticket_id}; status=planned")


def cmd_replay(run_id: str) -> None:
    """Print events for a past run as a timeline."""
    repo = _repo_root()
    conn = get_db(db_path(repo))
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


def cmd_lint() -> None:
    """Reconcile DB ↔ markdown ↔ filesystem; print mismatches."""
    repo = _repo_root()
    if not db_path(repo).exists():
        typer.secho("No murder.db — run murder init", err=True)
        raise typer.Exit(1)
    conn = get_db(db_path(repo))
    asyncio.run(PlanSync(repo, conn).reconcile_all())
    asyncio.run(TicketSync(repo, conn).reconcile_all())
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
    note_rows = {r["name"]: dict(r) for r in conn.execute("SELECT * FROM notes").fetchall()}
    for name, row in note_rows.items():
        md = repo / row["materialized_path"]
        if not md.exists():
            issues.append(f"note {name}: missing markdown {md}")
            continue
        text = md.read_text(encoding="utf-8")
        if text != str(row["body"]):
            issues.append(f"note {name}: DB/file body mismatch")
    if notes_dir(repo).exists():
        for md in notes_dir(repo).glob("*.md"):
            if md.stem not in note_rows:
                issues.append(f"note {md.stem}: orphan markdown {md}")
    rows = conn.execute("SELECT id FROM tickets").fetchall()
    tickets: list[Ticket] = []
    for r in rows:
        tid = r["id"]
        md = agents_dir(repo) / "tickets" / f"{tid}.md"
        if not md.exists():
            issues.append(f"ticket {tid}: missing markdown {md}")
        trow = get_ticket(conn, tid)
        if not trow:
            continue
        tickets.append(
            Ticket(
                id=trow["id"],
                title=trow["title"],
                status=TicketStatus(trow["status"]),
                harness=trow.get("harness"),
                model=trow.get("model"),
                attempts=trow["attempts"],
                created_at=datetime.fromisoformat(trow["created_at"]),
                updated_at=datetime.fromisoformat(trow["updated_at"]),
                deps=list(trow.get("deps") or []),
                skills=list(trow.get("skills") or []),
                checklist=[
                    ChecklistItem(
                        id=c.get("id"),
                        ord=c["ord"],
                        text=c["text"],
                        done=bool(c["done"]),
                        done_at=datetime.fromisoformat(c["done_at"]) if c.get("done_at") else None,
                    )
                    for c in trow.get("checklist") or []
                ],
            )
        )
    ticket_by_id = {ticket.id: ticket for ticket in tickets}
    for ticket in tickets:
        seen: set[str] = set()
        stack = list(ticket.deps)
        while stack:
            dep_id = stack.pop()
            if dep_id == ticket.id:
                issues.append(f"ticket {ticket.id}: dependency cycle")
                break
            if dep_id in seen:
                continue
            seen.add(dep_id)
            dep = ticket_by_id.get(dep_id)
            if dep is not None:
                stack.extend(dep.deps)
    conn.close()
    if issues:
        for i in issues:
            typer.echo(i)
        raise typer.Exit(1)
    typer.echo("lint: OK")
