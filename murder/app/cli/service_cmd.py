"""Service lifecycle and ticket-operation commands."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import typer

from murder.work.tickets.status import TicketStatus
from murder.config import Config
from murder.state.persistence.escalations import list_pending_escalations
from murder.state.persistence.schema import get_db, init_db
from murder.state.persistence.tickets import get_ticket
from murder.work.plans.sync import PlanSync, content_hash
from murder.app.service.host import ServiceHost
from murder.state.storage.filesystem import lock_is_held, read_lock_pid
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
from murder.app.cli._util import pid_is_alive as _pid_is_alive
from murder.app.cli._util import repo_root as _repo_root

LOGGER = logging.getLogger(__name__)

# Headroom for the daemon's boot file-descriptor burst (5 harness model-probe
# subprocesses + the startup rogue + tmux sessions + per-pane log tails +
# bus sockets all opening at once). The stock soft limit is 1024, which a cold
# boot can momentarily exceed -> EMFILE ("Too many open files"). Raise the soft
# limit toward this target, clamped to the inherited hard limit.
_FD_SOFT_LIMIT_TARGET = 4096


def _raise_fd_soft_limit(target: int = _FD_SOFT_LIMIT_TARGET) -> None:
    """Best-effort: raise this process's soft ``RLIMIT_NOFILE`` toward ``target``.

    Fail-soft — never block daemon startup over a limit we couldn't change.
    Clamped to the hard limit (raising the hard limit needs privileges we don't
    assume). No-op if the soft limit already meets the target.
    """
    try:
        import resource

        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        desired = min(target, hard)
        if soft >= desired:
            return
        resource.setrlimit(resource.RLIMIT_NOFILE, (desired, hard))
        LOGGER.info("raised RLIMIT_NOFILE soft limit %d -> %d (hard %d)", soft, desired, hard)
    except Exception:  # pragma: no cover - platform/permission edge; never fatal
        LOGGER.debug("could not raise RLIMIT_NOFILE", exc_info=True)


def apply_client_log_level(cli_value: str | None) -> None:
    """Resolve the client ``--log-level`` rung, propagate it, and configure logging.

    Shared by the bare-``murder`` (TUI) path and ``murder up``. Setting
    ``MURDER_LOG_LEVEL`` to the resolved RUNG (e.g. ``advanced``) BEFORE the
    service subprocess is spawned makes the inherited env carry the whole ladder
    position into ``serviced`` (which has no ``env=`` arg on its Popen) — from
    that single value the child derives both the python level AND whether to open
    the flight recorder. Then configures stderr-only NDJSON logging for this
    client process itself.
    """
    from murder.observability.logging_setup import (
        configure_logging,
        level_for_rung,
        resolve_rung,
    )

    rung = resolve_rung(cli_value)
    os.environ["MURDER_LOG_LEVEL"] = rung
    configure_logging(level=level_for_rung(rung), log_path=None)


def _open_existing_db(repo: Path):  # type: ignore[return]
    path = db_path(repo)
    if not path.exists():
        typer.secho("No murder.db — run murder init", err=True)
        raise typer.Exit(1)
    conn = get_db(path)
    init_db(conn)
    return conn


def _live_service_sessions() -> list[ServiceSession]:
    sessions: list[ServiceSession] = []
    for session in list_service_sessions():
        if _pid_is_alive(session.pid):
            sessions.append(session)
        else:
            remove_service_session(session.name)
    return sessions


async def _socket_is_connectable(socket_path: Path, *, timeout_s: float = 0.5) -> bool:
    # The application boundary is WebSocket-only.  Lifecycle probing is not an
    # application request and therefore deliberately does not open a client
    # protocol connection; the lock owner is the service authority.
    del socket_path, timeout_s
    return True


async def _supervisor_is_live(repo: Path, socket_path: Path) -> bool:
    pid = read_lock_pid(lock_path(repo))
    return bool(
        pid is not None and _pid_is_alive(pid) and await _socket_is_connectable(socket_path)
    )


def _live_lock_owner_pid(repo: Path) -> int | None:
    """Return the live pid recorded by the repo lock, if any.

    A live lock owner whose socket is not answering may still be in startup (or
    briefly have a busy event loop).  It is not safe to treat that state as
    permission to launch a second supervisor: the duplicate will lose the
    flock race and exit with code 1, obscuring the healthy process that won.
    """
    pid = read_lock_pid(lock_path(repo))
    return pid if pid is not None and _pid_is_alive(pid) and lock_is_held(lock_path(repo)) else None


def _spawn_service_process(repo: Path) -> subprocess.Popen[bytes]:
    log_root = logs_dir(repo) / datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    log_root.mkdir(parents=True, exist_ok=True)
    with open(log_root / "supervisor.ndjson", "ab", buffering=0) as log_file:
        return subprocess.Popen(
            [sys.executable, "-m", "murder", "serviced"],
            cwd=str(repo),
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )


async def _ensure_supervisor_impl(repo: Path, socket_path: Path) -> bool:
    """Ensure a responsive supervisor, returning whether this call started it."""
    if await _supervisor_is_live(repo, socket_path):
        return False

    proc: subprocess.Popen[bytes] | None = None
    delays = (0.25, 0.5, 1.0, 1.0, 1.0, 1.0)
    for delay in delays:
        # The repo lock is acquired before the socket opens.  Respect its live
        # owner during that readiness gap instead of spawning a doomed
        # duplicate.  If an owner dies while we wait, the next iteration takes
        # over startup.
        if proc is None and _live_lock_owner_pid(repo) is None:
            proc = _spawn_service_process(repo)

        await asyncio.sleep(delay)
        if await _supervisor_is_live(repo, socket_path):
            return proc is not None and read_lock_pid(lock_path(repo)) == proc.pid

        # Fail fast if the child already died (e.g. crashed on import) instead
        # of polling the full window for a process that's gone.  A code-1 child
        # can also mean a concurrent launcher won the flock race; in that case
        # follow the winner rather than surfacing the loser's exit status.
        if proc is not None:
            rc = proc.poll()
            if rc is not None:
                owner_pid = _live_lock_owner_pid(repo)
                if owner_pid is not None and owner_pid != proc.pid:
                    proc = None
                    continue
                raise RuntimeError(f"supervisor process exited during startup (code {rc})")
    raise RuntimeError("supervisor did not become ready within 5s")


async def _ensure_supervisor(repo: Path, socket_path: Path) -> None:
    await _ensure_supervisor_impl(repo, socket_path)


async def _ensure_supervisor_started(repo: Path, socket_path: Path) -> bool:
    """Return True when this call started the supervisor, False if it was already live."""
    return await _ensure_supervisor_impl(repo, socket_path)


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
        # Flattened to a CLI line for the expected lock/readiness cases, but log
        # the full traceback at DEBUG so a genuine programming RuntimeError isn't
        # silently swallowed.
        LOGGER.debug("service entry raised RuntimeError", exc_info=True)
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from e


async def _run_supervisor_only(websocket_port: int = 0) -> None:
    # Configure stderr logging immediately so early-startup records reach the
    # child's stdout/stderr -> supervisor.ndjson. The per-run service.log file
    # handler attaches later in Runtime.start once the run dir exists.
    from murder.observability.logging_setup import configure_logging, resolve_log_level

    configure_logging(level=resolve_log_level(), log_path=None)
    repo = _repo_root()
    cfg = Config.load(repo)
    host = ServiceHost(cfg, repo, websocket_port=websocket_port)
    async with host:
        try:
            await host.run_until_signal()
        finally:
            # A service shutdown is authoritative (`murder down`): the backend
            # owns agent/tmux teardown, not any connected client. Let Runtime
            # stop agents/tmux.
            with contextlib.suppress(Exception):
                if host.runtime is not None:
                    host.runtime.clear_shutdown_signal()


def cmd_serviced(
    websocket_port: int = typer.Option(0, "--websocket-port", help="Application WebSocket port; 0 = ephemeral."),
) -> None:
    """Internal supervisor-only service entrypoint."""
    _raise_fd_soft_limit()
    _run_async_entry(_run_supervisor_only(websocket_port=websocket_port))


def _signal_service(repo: Path, pid: int, *, session_name: str | None = None) -> None:
    # Re-read the live lock pid right before signalling so we don't SIGTERM a
    # recycled, unrelated process: between the session-registry read and here
    # the daemon may have exited and its pid been reused. Only signal if the
    # repo lock still names this exact pid; otherwise the old service is gone.
    current = read_lock_pid(lock_path(repo))
    if current != pid:
        if current is None:
            with contextlib.suppress(FileNotFoundError):
                lock_path(repo).unlink()
        if session_name is not None:
            remove_service_session(session_name)
        typer.echo(f"PID {pid} no longer holds the repo lock; nothing to signal.")
        return
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
    init_db(conn)
    try:
        typer.echo("Tickets by status:")
        for st in ("planned", "ready", "in_progress", "blocked", "done", "failed"):
            n = conn.execute(
                "SELECT COUNT(*) AS c FROM tickets WHERE status = ?", (st,)
            ).fetchone()["c"]
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
    finally:
        conn.close()


def cmd_reopen(ticket_id: str) -> None:
    """Mark a done ticket as planned and cascade to dependents (D7)."""
    repo = _repo_root()
    conn = get_db(db_path(repo))
    init_db(conn)
    try:
        cascaded = lifecycle.reopen(conn, ticket_id)
    except lifecycle.InvalidTransition as e:
        typer.secho(str(e), err=True)
        raise typer.Exit(1) from e
    finally:
        conn.close()
    typer.echo(f"Reopened {ticket_id}; cascaded: {', '.join(cascaded) if cascaded else '(none)'}")


def cmd_retry(ticket_id: str) -> None:
    """Retry a failed ticket — transition failed → planned and clear its last_error."""
    repo = _repo_root()
    conn = get_db(db_path(repo))
    init_db(conn)
    try:
        lifecycle.transition(conn, ticket_id, TicketStatus.PLANNED, reason="retry")
        lifecycle.clear_last_error(conn, ticket_id)
    except lifecycle.InvalidTransition as e:
        typer.secho(str(e), err=True)
        raise typer.Exit(1) from e
    finally:
        conn.close()
    typer.echo(f"Retried {ticket_id}; status=planned")


def cmd_replay(run_id: str) -> None:
    """Generic event replay was retired with the bus-as-API architecture."""
    del run_id
    typer.secho(
        "Generic event replay is retired. Inspect feature facts, activities, or projections instead.",
        err=True,
    )
    raise typer.Exit(2)


def cmd_lint() -> None:
    """Reconcile DB ↔ markdown ↔ filesystem; print mismatches."""
    repo = _repo_root()
    if not db_path(repo).exists():
        typer.secho("No murder.db — run murder init", err=True)
        raise typer.Exit(1)
    conn = get_db(db_path(repo))
    init_db(conn)
    try:
        _run_lint_checks(repo, conn)
    finally:
        conn.close()


def _run_lint_checks(repo: Path, conn) -> None:  # type: ignore[no-untyped-def]
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
    if issues:
        for i in issues:
            typer.echo(i)
        raise typer.Exit(1)
    typer.echo("lint: OK")
