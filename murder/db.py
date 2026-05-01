"""SQLite schema + access layer (D2).

`.agents/murder.db` is the source of truth for ticket metadata, status,
deps, write_sets, checklist items, agent state, events, escalations, and
runs. Markdown stays for prose only.

WAL mode is mandatory — Augur reads concurrently with Sentinel writes.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from murder.tickets.schema import Ticket

# fmt: off
SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS runs (
    run_id            TEXT PRIMARY KEY,
    started_at        TEXT NOT NULL,
    ended_at          TEXT,
    config_snapshot   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tickets (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    wave          INTEGER NOT NULL,
    status        TEXT NOT NULL CHECK (status IN
                  ('planned','ready','in_progress','blocked','done','failed')),
    harness       TEXT,
    model         TEXT,
    attempts      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tickets_wave   ON tickets(wave);
CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);

CREATE TABLE IF NOT EXISTS ticket_deps (
    ticket_id      TEXT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    depends_on_id  TEXT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    PRIMARY KEY (ticket_id, depends_on_id),
    CHECK (ticket_id != depends_on_id)
);

CREATE TABLE IF NOT EXISTS ticket_write_set (
    ticket_id  TEXT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    path       TEXT NOT NULL,
    PRIMARY KEY (ticket_id, path)
);

CREATE TABLE IF NOT EXISTS ticket_skills (
    ticket_id  TEXT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    skill      TEXT NOT NULL,
    PRIMARY KEY (ticket_id, skill)
);

CREATE TABLE IF NOT EXISTS checklist (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id  TEXT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    ord        INTEGER NOT NULL,
    text       TEXT NOT NULL,
    done       INTEGER NOT NULL DEFAULT 0,
    done_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_checklist_ticket ON checklist(ticket_id);

CREATE TABLE IF NOT EXISTS agents (
    agent_id          TEXT PRIMARY KEY,
    role              TEXT NOT NULL CHECK (role IN
                      ('collaborator','sentinel','augur','monkey')),
    ticket_id         TEXT REFERENCES tickets(id) ON DELETE SET NULL,
    session           TEXT,
    status            TEXT NOT NULL CHECK (status IN
                      ('idle','running','blocked','escalating','done','dead')),
    start_commit      TEXT,
    started_at        TEXT NOT NULL,
    last_heartbeat_at TEXT,
    pid               INTEGER
);

CREATE TABLE IF NOT EXISTS events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT NOT NULL,
    run_id          TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    agent_id        TEXT,
    role            TEXT,
    ticket_id       TEXT,
    type            TEXT NOT NULL,
    payload_json    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_run    ON events(run_id);
CREATE INDEX IF NOT EXISTS idx_events_ticket ON events(ticket_id);
CREATE INDEX IF NOT EXISTS idx_events_type   ON events(type);

CREATE TABLE IF NOT EXISTS escalations (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                TEXT NOT NULL,
    ticket_id         TEXT REFERENCES tickets(id) ON DELETE SET NULL,
    severity          INTEGER NOT NULL CHECK (severity BETWEEN 1 AND 3),
    reason            TEXT NOT NULL,
    to_recipient      TEXT NOT NULL CHECK (to_recipient IN ('user','collaborator')),
    resolved          INTEGER NOT NULL DEFAULT 0,
    resolved_at       TEXT,
    source_event_id   INTEGER REFERENCES events(id) ON DELETE SET NULL,
    body_path         TEXT
);
"""
# fmt: on


def connect(db_path: Path) -> sqlite3.Connection:
    """Open a SQLite connection in WAL mode with sane pragmas."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # isolation_level=None for explicit BEGIN/COMMIT control via executescript.
    conn = sqlite3.connect(
        str(db_path),
        isolation_level=None,
        check_same_thread=False,  # asyncio uses loop's default executor; serialize at app layer
        timeout=10.0,
    )
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        PRAGMA journal_mode = WAL;
        PRAGMA synchronous = NORMAL;
        PRAGMA foreign_keys = ON;
        PRAGMA busy_timeout = 5000;
        """
    )
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Apply SCHEMA_SQL idempotently."""
    conn.executescript(SCHEMA_SQL)


def db_path_for(repo_root: Path) -> Path:
    return repo_root / ".agents" / "murder.db"


# --- Iso timestamp helper ---------------------------------------------------

def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


# --- Runs -------------------------------------------------------------------

def insert_run(conn: sqlite3.Connection, run_id: str, config_snapshot: str) -> None:
    conn.execute(
        "INSERT INTO runs(run_id, started_at, config_snapshot) VALUES (?, ?, ?)",
        (run_id, _now(), config_snapshot),
    )


def end_run(conn: sqlite3.Connection, run_id: str) -> None:
    conn.execute("UPDATE runs SET ended_at = ? WHERE run_id = ?", (_now(), run_id))


# --- Tickets ----------------------------------------------------------------

def insert_ticket(conn: sqlite3.Connection, ticket: "Ticket") -> None:
    """Insert ticket + its child rows in one transaction."""
    now = _now()
    conn.execute("BEGIN")
    try:
        conn.execute(
            """
            INSERT INTO tickets(id, title, wave, status, harness, model, attempts,
                                created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ticket.id,
                ticket.title,
                ticket.wave,
                ticket.status.value,
                ticket.harness,
                ticket.model,
                ticket.attempts,
                ticket.created_at.isoformat(timespec="seconds"),
                now,
            ),
        )
        for dep in ticket.deps:
            conn.execute(
                "INSERT INTO ticket_deps(ticket_id, depends_on_id) VALUES (?, ?)",
                (ticket.id, dep),
            )
        for path in ticket.write_set:
            conn.execute(
                "INSERT INTO ticket_write_set(ticket_id, path) VALUES (?, ?)",
                (ticket.id, str(path)),
            )
        for skill in ticket.skills:
            conn.execute(
                "INSERT INTO ticket_skills(ticket_id, skill) VALUES (?, ?)",
                (ticket.id, skill),
            )
        for item in ticket.checklist:
            conn.execute(
                "INSERT INTO checklist(ticket_id, ord, text, done) VALUES (?, ?, ?, ?)",
                (ticket.id, item.ord, item.text, 1 if item.done else 0),
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def get_ticket(conn: sqlite3.Connection, ticket_id: str) -> dict[str, Any] | None:
    """Return a dict of ticket + its child rows, or None."""
    row = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    if row is None:
        return None
    deps = [
        r["depends_on_id"]
        for r in conn.execute(
            "SELECT depends_on_id FROM ticket_deps WHERE ticket_id = ?", (ticket_id,)
        )
    ]
    write_set = [
        r["path"]
        for r in conn.execute(
            "SELECT path FROM ticket_write_set WHERE ticket_id = ?", (ticket_id,)
        )
    ]
    skills = [
        r["skill"]
        for r in conn.execute(
            "SELECT skill FROM ticket_skills WHERE ticket_id = ?", (ticket_id,)
        )
    ]
    checklist = [
        dict(r)
        for r in conn.execute(
            "SELECT id, ord, text, done, done_at FROM checklist "
            "WHERE ticket_id = ? ORDER BY ord",
            (ticket_id,),
        )
    ]
    return {
        **dict(row),
        "deps": deps,
        "write_set": write_set,
        "skills": skills,
        "checklist": checklist,
    }


def list_tickets_by_status(
    conn: sqlite3.Connection, status: str
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id FROM tickets WHERE status = ? ORDER BY wave, id", (status,)
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        t = get_ticket(conn, r["id"])
        if t is not None:
            out.append(t)
    return out


def list_tickets_in_wave(conn: sqlite3.Connection, wave: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id FROM tickets WHERE wave = ? ORDER BY id", (wave,)
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        t = get_ticket(conn, r["id"])
        if t is not None:
            out.append(t)
    return out


def update_ticket_status(
    conn: sqlite3.Connection, ticket_id: str, new_status: str
) -> None:
    conn.execute(
        "UPDATE tickets SET status = ?, updated_at = ? WHERE id = ?",
        (new_status, _now(), ticket_id),
    )


def get_ticket_status(conn: sqlite3.Connection, ticket_id: str) -> str | None:
    row = conn.execute(
        "SELECT status FROM tickets WHERE id = ?", (ticket_id,)
    ).fetchone()
    return row["status"] if row else None


def compute_ready(conn: sqlite3.Connection) -> list[str]:
    """Tickets whose deps are all `done` and that are currently `planned` or `ready`.

    A ticket with no deps qualifies trivially. Result is sorted by wave then id
    so kickoff order is stable.
    """
    rows = conn.execute(
        """
        SELECT t.id
          FROM tickets AS t
          WHERE t.status IN ('planned', 'ready')
            AND NOT EXISTS (
                SELECT 1 FROM ticket_deps AS d
                  JOIN tickets AS dep ON dep.id = d.depends_on_id
                 WHERE d.ticket_id = t.id
                   AND dep.status != 'done'
            )
          ORDER BY t.wave, t.id
        """
    ).fetchall()
    return [r["id"] for r in rows]


def dependents_of(conn: sqlite3.Connection, ticket_id: str) -> list[str]:
    """Tickets that directly depend on `ticket_id`."""
    rows = conn.execute(
        "SELECT ticket_id FROM ticket_deps WHERE depends_on_id = ?", (ticket_id,)
    ).fetchall()
    return [r["ticket_id"] for r in rows]


# --- Checklist (D6) ---------------------------------------------------------

def set_checklist(conn: sqlite3.Connection, ticket_id: str, items: list[str]) -> None:
    """Replace a ticket's checklist. Used by Collaborator on carve."""
    conn.execute("BEGIN")
    try:
        conn.execute("DELETE FROM checklist WHERE ticket_id = ?", (ticket_id,))
        for ord_, text in enumerate(items):
            conn.execute(
                "INSERT INTO checklist(ticket_id, ord, text, done) VALUES (?, ?, ?, 0)",
                (ticket_id, ord_, text),
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def check_off_item(conn: sqlite3.Connection, ticket_id: str, item_text: str) -> bool:
    """Mark first matching unchecked item as done; return True iff matched."""
    row = conn.execute(
        """
        SELECT id FROM checklist
         WHERE ticket_id = ? AND done = 0 AND text = ?
         ORDER BY ord LIMIT 1
        """,
        (ticket_id, item_text),
    ).fetchone()
    if row is None:
        return False
    conn.execute(
        "UPDATE checklist SET done = 1, done_at = ? WHERE id = ?",
        (_now(), row["id"]),
    )
    return True


def all_checked(conn: sqlite3.Connection, ticket_id: str) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM checklist WHERE ticket_id = ? AND done = 0",
        (ticket_id,),
    ).fetchone()
    return int(row["n"]) == 0


def checklist_progress(conn: sqlite3.Connection, ticket_id: str) -> tuple[int, int]:
    row = conn.execute(
        """
        SELECT
            SUM(CASE WHEN done = 1 THEN 1 ELSE 0 END) AS done_n,
            COUNT(*) AS total
          FROM checklist WHERE ticket_id = ?
        """,
        (ticket_id,),
    ).fetchone()
    return int(row["done_n"] or 0), int(row["total"] or 0)


# --- Agents -----------------------------------------------------------------

def upsert_agent(
    conn: sqlite3.Connection,
    *,
    agent_id: str,
    role: str,
    ticket_id: str | None,
    session: str | None,
    status: str,
    start_commit: str | None = None,
    pid: int | None = None,
) -> None:
    """Insert or update an agent row."""
    now = _now()
    existing = conn.execute(
        "SELECT 1 FROM agents WHERE agent_id = ?", (agent_id,)
    ).fetchone()
    if existing is None:
        conn.execute(
            """
            INSERT INTO agents
                (agent_id, role, ticket_id, session, status, start_commit,
                 started_at, last_heartbeat_at, pid)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (agent_id, role, ticket_id, session, status, start_commit, now, now, pid),
        )
    else:
        conn.execute(
            """
            UPDATE agents
               SET role = ?, ticket_id = ?, session = ?, status = ?,
                   start_commit = COALESCE(?, start_commit),
                   last_heartbeat_at = ?,
                   pid = COALESCE(?, pid)
             WHERE agent_id = ?
            """,
            (role, ticket_id, session, status, start_commit, now, pid, agent_id),
        )


def heartbeat_agent(conn: sqlite3.Connection, agent_id: str) -> None:
    conn.execute(
        "UPDATE agents SET last_heartbeat_at = ? WHERE agent_id = ?",
        (_now(), agent_id),
    )


def set_agent_status(conn: sqlite3.Connection, agent_id: str, status: str) -> None:
    conn.execute(
        "UPDATE agents SET status = ?, last_heartbeat_at = ? WHERE agent_id = ?",
        (status, _now(), agent_id),
    )


# --- Events -----------------------------------------------------------------

def insert_event(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    agent_id: str,
    role: str,
    ticket_id: str | None,
    type: str,
    payload: dict[str, Any],
    ts: str | None = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO events(ts, run_id, agent_id, role, ticket_id, type, payload_json)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ts or _now(),
            run_id,
            agent_id,
            role,
            ticket_id,
            type,
            json.dumps(payload, default=str),
        ),
    )
    return int(cur.lastrowid or 0)


# --- Escalations ------------------------------------------------------------

def insert_escalation(
    conn: sqlite3.Connection,
    *,
    ticket_id: str | None,
    severity: int,
    reason: str,
    to_recipient: str,
    source_event_id: int | None = None,
    body_path: str | None = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO escalations
            (ts, ticket_id, severity, reason, to_recipient, source_event_id, body_path)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (_now(), ticket_id, severity, reason, to_recipient, source_event_id, body_path),
    )
    return int(cur.lastrowid or 0)


def list_pending_escalations(
    conn: sqlite3.Connection, recipient: str | None = None
) -> list[dict[str, Any]]:
    if recipient is None:
        rows = conn.execute(
            "SELECT * FROM escalations WHERE resolved = 0 ORDER BY ts DESC"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM escalations WHERE resolved = 0 AND to_recipient = ? "
            "ORDER BY ts DESC",
            (recipient,),
        ).fetchall()
    return [dict(r) for r in rows]


def resolve_escalation(conn: sqlite3.Connection, escalation_id: int) -> None:
    conn.execute(
        "UPDATE escalations SET resolved = 1, resolved_at = ? WHERE id = ?",
        (_now(), escalation_id),
    )
