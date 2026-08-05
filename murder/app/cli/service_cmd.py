"""Service lifecycle and ticket-operation commands."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import typer

from murder.app.cli._util import pid_is_alive as _pid_is_alive
from murder.app.cli._util import repo_root as _repo_root
from murder.app.protocol.common import DAEMON_WEBSOCKET_HOST, DAEMON_WEBSOCKET_PORT
from murder.app.service.daemon_host import (
    DaemonHost,
    daemon_lock_path,
    live_daemon_pid,
    probe_daemon_listener,
)
from murder.app.service.repository_manager import RecentRepository, RepositoryManager
from murder.state.persistence.connection import RepoDb, open_repo_db
from murder.state.persistence.escalations import list_pending_escalations
from murder.state.persistence.tickets import get_ticket
from murder.state.storage.filesystem import read_lock_pid
from murder.state.storage.paths import (
    agents_dir,
    notes_dir,
    plans_dir,
)
from murder.state.storage.service_registry import (
    project_session_name,
    read_daemon_record,
    remove_daemon_record,
)
from murder.user_config import config_dir
from murder.work.plans.sync import PlanSync, content_hash
from murder.work.tickets import lifecycle
from murder.work.tickets.schema import ChecklistItem, Ticket
from murder.work.tickets.status import TicketStatus
from murder.work.tickets.sync import TicketSync

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
    if not agents_dir(repo).exists():
        typer.secho("No murder.db — run murder init", err=True)
        raise typer.Exit(1)
    return open_repo_db(repo)


def _daemon_http_base() -> str:
    return f"http://{DAEMON_WEBSOCKET_HOST}:{DAEMON_WEBSOCKET_PORT}"


def _live_lock_owner_pid() -> int | None:
    """Return the live daemon pid, if any.

    A live lock owner whose socket is not answering may still be in startup (or
    briefly have a busy event loop).  It is not safe to treat that state as
    permission to launch a second daemon: the duplicate will lose the flock race
    and exit with code 1, obscuring the healthy process that won.
    """
    return live_daemon_pid()


def _spawn_daemon_process(*, cwd: Path | None = None) -> subprocess.Popen[bytes]:
    """Spawn ``murder serviced`` once; logs go under the user config directory."""
    log_root = config_dir() / "logs" / datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    log_root.mkdir(parents=True, exist_ok=True)
    with open(log_root / "daemon.ndjson", "ab", buffering=0) as log_file:
        return subprocess.Popen(
            [sys.executable, "-m", "murder", "serviced"],
            cwd=str(cwd) if cwd is not None else None,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )


async def _daemon_is_ready() -> bool:
    """True when a live murder daemon is accepting on the fixed listener port."""
    return await probe_daemon_listener()


async def _ensure_daemon_impl(*, spawn_cwd: Path | None = None) -> bool:
    """Make sure the daemon is accepting on ``:62077``. Return whether we started it."""
    if await _daemon_is_ready():
        return False

    proc: subprocess.Popen[bytes] | None = None
    # Cold boots can spend several seconds reconciling persisted harness state
    # before the application listener is bound.  Keep the quick initial probes,
    # then allow enough time for that bounded startup work to finish.
    delays = (0.25, 0.5, *(1.0 for _ in range(30)))
    for delay in delays:
        # The daemon lock is acquired before the socket opens.  Respect its live
        # owner during that readiness gap instead of spawning a doomed
        # duplicate.  If an owner dies while we wait, the next iteration takes
        # over startup.
        if proc is None and _live_lock_owner_pid() is None:
            proc = _spawn_daemon_process(cwd=spawn_cwd)

        await asyncio.sleep(delay)
        if await _daemon_is_ready():
            return proc is not None and live_daemon_pid() == proc.pid

        # Fail fast if the child already died (e.g. crashed on import) instead
        # of polling the full window for a process that's gone.  A code-1 child
        # can also mean a concurrent launcher won the flock race. In that case
        # follow the winner rather than surfacing the loser's exit status.
        if proc is not None:
            rc = proc.poll()
            if rc is not None:
                owner_pid = _live_lock_owner_pid()
                if owner_pid is not None and owner_pid != proc.pid:
                    proc = None
                    continue
                raise RuntimeError(f"daemon process exited during startup (code {rc})")
    raise RuntimeError("murder daemon did not become ready within 30s")


async def _ensure_daemon_started(*, spawn_cwd: Path | None = None) -> bool:
    """Return True when this call started the daemon, False if it was already live."""
    return await _ensure_daemon_impl(spawn_cwd=spawn_cwd)


async def _post_daemon_json(path: str, body: dict) -> dict:
    """POST JSON to the local daemon HTTP API and return the decoded body."""
    from aiohttp import ClientSession

    url = f"{_daemon_http_base()}{path}"
    async with ClientSession() as session:
        async with session.post(url, json=body) as resp:
            text = await resp.text()
            if resp.status >= 400:
                raise RuntimeError(f"daemon {path} failed ({resp.status}): {text}")
            if not text:
                return {}
            try:
                payload = json.loads(text)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"daemon {path} returned non-JSON ({resp.status}): {text[:200]}"
                ) from exc
            if not isinstance(payload, dict):
                raise RuntimeError(f"daemon {path} returned non-object JSON")
            return payload


async def _activate_repository(repo: Path) -> dict:
    """Ask the running daemon to activate ``repo`` (idempotent)."""
    return await _post_daemon_json(
        "/api/repos/activate",
        {"path": str(repo.resolve(strict=False))},
    )


async def _deactivate_repository(repo: Path) -> dict:
    """Ask the running daemon to deactivate one repository host."""
    return await _post_daemon_json(
        "/api/repos/deactivate",
        {"path": str(repo.resolve(strict=False))},
    )


async def ensure_daemon_and_activate(repo: Path) -> tuple[bool, dict]:
    """Ensure the daemon is up, then activate ``repo``. Returns (started, activate body)."""
    started = await _ensure_daemon_started(spawn_cwd=repo)
    info = await _activate_repository(repo)
    return started, info


def _friendly_lock_message() -> str:
    pid = live_daemon_pid()
    pid_text = f" (PID {pid})" if pid is not None else ""
    return (
        f"murder daemon is already running{pid_text}.\n"
        "Stop it with `murder down`, or run from inside the running TUI."
    )


def _run_async_entry(coro) -> None:  # type: ignore[no-untyped-def]
    try:
        asyncio.run(coro)
    except BlockingIOError:
        typer.secho(_friendly_lock_message(), fg=typer.colors.RED, err=True)
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
    # child's stdout/stderr -> daemon.ndjson. Per-repo run logs attach later
    # inside each RepositoryHost / ProcessScope.
    from murder.observability.logging_setup import configure_logging, resolve_log_level

    configure_logging(level=resolve_log_level(), log_path=None)
    repo = _repo_root()
    # ``0`` historically meant ephemeral; the single-daemon architecture binds
    # the fixed port unless an explicit override is passed (tests).
    port = DAEMON_WEBSOCKET_PORT if websocket_port == 0 else websocket_port
    await DaemonHost().run(initial_repo=repo, port=port)


def cmd_serviced(
    websocket_port: int = typer.Option(
        0,
        "--websocket-port",
        help=(
            f"Application WebSocket port; 0 = fixed daemon port "
            f"({DAEMON_WEBSOCKET_PORT})."
        ),
    ),
) -> None:
    """Internal supervisor-only service entrypoint."""
    _raise_fd_soft_limit()
    _run_async_entry(_run_supervisor_only(websocket_port=websocket_port))

_DOWN_WAIT_S = 5.0
_DOWN_POLL_S = 0.1


def _signal_daemon(pid: int) -> None:
    # Re-read the live daemon pid right before signalling so we don't SIGTERM a
    # recycled, unrelated process: between the registry/lock read and here the
    # daemon may have exited and its pid been reused. Only signal if the daemon
    # lock still names this exact pid. Otherwise the old service is gone.
    current = live_daemon_pid()
    if current != pid:
        if current is None:
            with contextlib.suppress(FileNotFoundError):
                daemon_lock_path().unlink()
            remove_daemon_record()
        typer.echo(f"PID {pid} no longer holds the daemon lock; nothing to signal.")
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        with contextlib.suppress(FileNotFoundError):
            daemon_lock_path().unlink()
        remove_daemon_record()
        typer.echo(f"Removed stale daemon lock for dead PID {pid}.")
        return
    typer.echo(f"Sent SIGTERM to pid {pid}")

    # Wait for a clean exit. Escalate if the daemon hangs after removing its
    # registry record (clients would otherwise see lock-held-but-no-listener).
    deadline = time.monotonic() + _DOWN_WAIT_S
    while time.monotonic() < deadline:
        if not _pid_is_alive(pid):
            break
        time.sleep(_DOWN_POLL_S)
    else:
        if _pid_is_alive(pid) and live_daemon_pid() == pid:
            try:
                os.kill(pid, signal.SIGKILL)
                typer.echo(f"Sent SIGKILL to pid {pid}")
            except ProcessLookupError:
                pass
            # Brief grace so the kernel reaps before we inspect the lock.
            kill_deadline = time.monotonic() + 1.0
            while time.monotonic() < kill_deadline and _pid_is_alive(pid):
                time.sleep(_DOWN_POLL_S)

    if not _pid_is_alive(pid):
        with contextlib.suppress(FileNotFoundError):
            if read_lock_pid(daemon_lock_path()) in (None, pid):
                daemon_lock_path().unlink()
        remove_daemon_record()


def cmd_down() -> None:
    """Stop the murder daemon (deactivates all repository hosts)."""
    pid = live_daemon_pid()
    if pid is None:
        if read_daemon_record() is not None:
            remove_daemon_record()
            typer.echo("Removed stale daemon registry record.")
            return
        typer.secho("No murder daemon running.", err=True)
        raise typer.Exit(1)
    _signal_daemon(pid)


def cmd_id() -> None:
    """Print the current directory's path-derived murder id."""
    typer.echo(project_session_name(_repo_root()))


async def _fetch_repo_list() -> list[dict] | None:
    """Return ``GET /api/repos`` rows when the daemon is up, else None."""
    from aiohttp import ClientSession

    if not await _daemon_is_ready():
        return None
    url = f"{_daemon_http_base()}/api/repos"
    try:
        async with ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status >= 400:
                    return None
                payload = await resp.json()
    except Exception:
        return None
    rows = payload.get("repositories") if isinstance(payload, dict) else None
    return list(rows) if isinstance(rows, list) else None


def _list_repositories_offline() -> list[RecentRepository]:
    return RepositoryManager().list_recent()


def cmd_ls() -> None:
    """List known repositories and which hosts are active in the daemon."""
    rows = asyncio.run(_fetch_repo_list())
    if rows is None:
        offline = _list_repositories_offline()
        if not offline:
            typer.echo("No murder repositories registered.")
            return
        typer.echo(f"{'ACTIVE':<8} {'REPOSITORY_ID':<38}  ROOT")
        for entry in offline:
            typer.echo(f"{'no':<8} {entry.repository_id:<38}  {entry.root_path}")
        return

    if not rows:
        typer.echo("No murder repositories registered.")
        return
    typer.echo(f"{'ACTIVE':<8} {'REPOSITORY_ID':<38}  ROOT")
    for row in rows:
        active = "yes" if row.get("active") else "no"
        rid = str(row.get("repository_id") or "")
        root = str(row.get("root_path") or "")
        typer.echo(f"{active:<8} {rid:<38}  {root}")


repo_app = typer.Typer(help="Per-repository daemon host controls.")


@repo_app.command("stop")
def cmd_repo_stop(
    path: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
        help="Repository checkout to deactivate (daemon stays running).",
    ),
) -> None:
    """Deactivate one repository host inside the running daemon."""

    async def _stop() -> None:
        if not await _daemon_is_ready():
            raise RuntimeError("murder daemon is not running")
        await _deactivate_repository(path)
        typer.echo(f"stopped {path}")

    _run_async_entry(_stop())


def cmd_status() -> None:
    """Print a concise status snapshot (no TUI)."""
    repo = _repo_root()
    if not agents_dir(repo).exists():
        typer.echo("No database — murder init")
        return
    conn = open_repo_db(repo)
    try:
        typer.echo("Tickets by status:")
        for st in ("planned", "ready", "in_progress", "blocked", "done", "failed"):
            n = conn.conn.execute(
                "SELECT COUNT(*) AS c FROM tickets WHERE repository_id = ? AND status = ?",
                (conn.repository_id, st),
            ).fetchone()["c"]
            typer.echo(f"  {st}: {n}")
        typer.echo("Agents:")
        for r in conn.conn.execute(
            "SELECT agent_id, role, ticket_id, status FROM agents "
            "WHERE repository_id = ? ORDER BY started_at DESC LIMIT 20",
            (conn.repository_id,),
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
    conn = open_repo_db(repo)
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
    conn = open_repo_db(repo)
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
    """Reconcile DB ↔ markdown ↔ filesystem. Print mismatches."""
    repo = _repo_root()
    if not agents_dir(repo).exists():
        typer.secho("No murder.db — run murder init", err=True)
        raise typer.Exit(1)
    conn = open_repo_db(repo)
    try:
        _run_lint_checks(repo, conn)
    finally:
        conn.close()


def _run_lint_checks(repo: Path, conn: RepoDb) -> None:
    asyncio.run(PlanSync(repo, conn).reconcile_all())
    asyncio.run(TicketSync(repo, conn).reconcile_all())
    issues: list[str] = []
    plan_rows = {
        r["name"]: dict(r)
        for r in conn.conn.execute(
            "SELECT * FROM plans WHERE repository_id = ?", (conn.repository_id,)
        ).fetchall()
    }
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
    note_rows = {
        r["name"]: dict(r)
        for r in conn.conn.execute(
            "SELECT * FROM notes WHERE repository_id = ?", (conn.repository_id,)
        ).fetchall()
    }
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
    rows = conn.conn.execute(
        "SELECT id FROM tickets WHERE repository_id = ?", (conn.repository_id,)
    ).fetchall()
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
