"""Roster persistence and snapshot assembly.

The roster is deliberately a read/write feature over the existing agent and
escalation tables. This repository owns the SQL and the transactional boundary
between an agent row and the roster refresh input.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid5

from murder.app.protocol.read_models import CrowSessionSummary, CrowSnapshot, InvalidationKeys
from murder.facts.contracts import ProjectionInputDraft
from murder.facts.log import append_projection_input
from murder.state.persistence.connection import RepoDb

_ROSTER_PROJECTION_NAMESPACE = UUID("d56cf25f-4a1b-4e34-a3c2-3f73893a4d7d")


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def _optional_str(value: object) -> str | None:
    return None if value is None else str(value)


def _parse_datetime(value: object) -> datetime | None:
    return None if value is None else datetime.fromisoformat(str(value))


def _keep_failed_session(session: CrowSessionSummary, *, now: datetime) -> bool:
    """Keep fresh failures and failures whose ticket still needs attention."""

    if session.status != "failed":
        return True
    if session.ticket_status not in {None, "", "done", "archived"}:
        return True
    if session.last_seen is None:
        return False
    return now - session.last_seen <= timedelta(hours=2)


class RosterRepository:
    """Deep roster data module: agent writes and roster view reads."""

    def snapshot(self, db: RepoDb) -> CrowSnapshot:
        conn = db.conn
        as_of = datetime.utcnow()
        rows = conn.execute(
            """
            SELECT a.agent_id, a.role, a.ticket_id, a.status, a.session,
                   (
                     SELECT hs.session_id
                       FROM harness_sessions hs
                      WHERE hs.repository_id = a.repository_id
                        AND hs.transport = 'tmux'
                        AND hs.transport_ref = a.session
                        AND hs.status NOT IN ('stopped','failed','lost')
                      ORDER BY hs.started_at DESC, hs.session_id DESC
                      LIMIT 1
                   ) AS persistent_session_id,
                   COALESCE(a.harness, t.harness) AS harness,
                   COALESCE(a.model, t.model) AS model,
                   a.worktree_path,
                   a.started_at, a.last_heartbeat_at,
                   COALESCE(t.title, '') AS title,
                   COALESCE(t.status, '') AS ticket_status
              FROM agents a
              LEFT JOIN tickets t ON t.repository_id = a.repository_id AND t.id = a.ticket_id
             WHERE a.repository_id = ? AND a.status NOT IN ('done', 'dead')
             ORDER BY
                   CASE a.status
                     WHEN 'escalating' THEN 0
                     WHEN 'blocked' THEN 1
                     WHEN 'running' THEN 2
                     WHEN 'idle' THEN 3
                     WHEN 'failed' THEN 4
                     ELSE 5
                   END,
                   a.started_at DESC,
                   a.agent_id
            """,
            (db.repository_id,),
        ).fetchall()
        ticket_ids = [str(row["ticket_id"]) for row in rows if row["ticket_id"]]
        open_by_ticket: dict[str, tuple[int, int]] = {}
        if ticket_ids:
            placeholders = ",".join("?" * len(ticket_ids))
            for escalation in conn.execute(
                f"""
                SELECT ticket_id, COUNT(*) AS n, MAX(severity) AS max_sev
                  FROM escalations
                 WHERE repository_id = ? AND resolved = 0 AND ticket_id IN ({placeholders})
                 GROUP BY ticket_id
                """,
                (db.repository_id, *ticket_ids),
            ).fetchall():
                open_by_ticket[str(escalation["ticket_id"])] = (
                    int(escalation["n"]),
                    int(escalation["max_sev"] or 0),
                )
        sessions = tuple(
            CrowSessionSummary(
                agent_id=str(row["agent_id"]),
                role=str(row["role"]),
                ticket_id=_optional_str(row["ticket_id"]),
                ticket_title=str(row["title"] or ""),
                status=str(row["status"]),
                display_name=_optional_str(row["session"]),
                harness=_optional_str(row["harness"]),
                last_seen=_parse_datetime(row["last_heartbeat_at"]),
                started_at=_parse_datetime(row["started_at"]),
                ticket_status=_optional_str(row["ticket_status"]),
                worktree_path=_optional_str(row["worktree_path"]),
                model=_optional_str(row["model"]),
                open_escalations=open_by_ticket.get(str(row["ticket_id"] or ""), (0, 0))[0],
                max_severity=open_by_ticket.get(str(row["ticket_id"] or ""), (0, 0))[1],
                session_id=_optional_str(row["persistent_session_id"]),
            )
            for row in rows
        )
        return CrowSnapshot(
            sessions=tuple(
                session for session in sessions if _keep_failed_session(session, now=as_of)
            ),
            as_of=as_of,
            invalidation_key=self._invalidation_key(db),
        )

    def sync_agent(
        self,
        db: RepoDb,
        *,
        agent_id: str,
        role: str,
        ticket_id: str | None,
        session: str | None,
        harness: str | None,
        model: str | None,
        status: str,
        start_commit: str | None,
        worktree_path: str | None,
        pid: int | None,
    ) -> None:
        """Persist an agent and its roster invalidation in one transaction."""

        conn = db.conn
        owns_transaction = conn.isolation_level is None and not conn.in_transaction
        if owns_transaction:
            conn.execute("BEGIN IMMEDIATE")
        try:
            self._upsert_agent(
                db,
                agent_id=agent_id,
                role=role,
                ticket_id=ticket_id,
                session=session,
                harness=harness,
                model=model,
                status=status,
                start_commit=start_commit,
                worktree_path=worktree_path,
                pid=pid,
            )
            self.invalidate(db, subject_key=agent_id)
        except BaseException:
            if owns_transaction:
                conn.rollback()
            raise
        else:
            if owns_transaction:
                conn.commit()

    def invalidate(self, db: RepoDb, *, subject_key: str) -> None:
        """Append a roster refresh input in the caller's transaction."""

        row = db.conn.execute(
            """
            SELECT COALESCE(MAX(generation), -1) + 1 AS next_gen
              FROM projection_inputs
             WHERE repository_id = ? AND projection = 'roster' AND subject_key = ?
            """,
            (db.repository_id, subject_key),
        ).fetchone()
        generation = int(row["next_gen"])
        append_projection_input(
            db,
            ProjectionInputDraft(
                input_id=uuid5(
                    _ROSTER_PROJECTION_NAMESPACE,
                    f"{subject_key}:{generation}",
                ),
                projection="roster",
                subject_key=subject_key,
                generation=generation,
            ),
            created_at=datetime.now(timezone.utc),
        )

    def set_agent_status(
        self, db: RepoDb, *, agent_id: str, status: str
    ) -> None:
        self._mutate_agent(
            db,
            agent_id=agent_id,
            sql=(
                "UPDATE agents SET status = ?, last_heartbeat_at = ? "
                "WHERE repository_id = ? AND agent_id = ?"
            ),
            params=(status, _now(), db.repository_id, agent_id),
        )

    def heartbeat_agent(
        self, db: RepoDb, *, agent_id: str, invalidate: bool
    ) -> None:
        conn = db.conn
        owns_transaction = conn.isolation_level is None and not conn.in_transaction
        if owns_transaction:
            conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                "UPDATE agents SET last_heartbeat_at = ? WHERE repository_id = ? AND agent_id = ?",
                (_now(), db.repository_id, agent_id),
            )
            if invalidate:
                self.invalidate(db, subject_key=agent_id)
        except BaseException:
            if owns_transaction:
                conn.rollback()
            raise
        else:
            if owns_transaction:
                conn.commit()

    def _invalidation_key(self, db: RepoDb) -> str:
        row = db.conn.execute(
            """
            SELECT COALESCE(MAX(sequence), 0) AS sequence
              FROM projection_inputs
             WHERE repository_id = ? AND projection = 'roster'
            """
        , (db.repository_id,)).fetchone()
        return f"{InvalidationKeys.crows}-{int(row['sequence'])}"

    def _mutate_agent(
        self,
        db: RepoDb,
        *,
        agent_id: str,
        sql: str,
        params: tuple[object, ...],
    ) -> None:
        conn = db.conn
        owns_transaction = conn.isolation_level is None and not conn.in_transaction
        if owns_transaction:
            conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(sql, params)
            self.invalidate(db, subject_key=agent_id)
        except BaseException:
            if owns_transaction:
                conn.rollback()
            raise
        else:
            if owns_transaction:
                conn.commit()

    @staticmethod
    def _upsert_agent(
        db: RepoDb,
        *,
        agent_id: str,
        role: str,
        ticket_id: str | None,
        session: str | None,
        harness: str | None,
        model: str | None,
        status: str,
        start_commit: str | None,
        worktree_path: str | None,
        pid: int | None,
    ) -> None:
        now = _now()
        conn = db.conn
        existing = conn.execute(
            "SELECT 1 FROM agents WHERE repository_id = ? AND agent_id = ?",
            (db.repository_id, agent_id),
        ).fetchone()
        if existing is None:
            conn.execute(
                """
                INSERT INTO agents
                    (repository_id, agent_id, role, ticket_id, session, harness, model,
                     worktree_path, status,
                     start_commit, started_at, last_heartbeat_at, pid)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (db.repository_id, agent_id, role, ticket_id, session, harness, model,
                 worktree_path, status,
                 start_commit, now, now, pid),
            )
            return
        conn.execute(
            """
            UPDATE agents
               SET role = ?, ticket_id = ?, session = ?, harness = COALESCE(?, harness),
                   model = COALESCE(?, model), worktree_path = COALESCE(?, worktree_path),
                   status = ?, start_commit = COALESCE(?, start_commit),
                   last_heartbeat_at = ?, pid = COALESCE(?, pid)
             WHERE repository_id = ? AND agent_id = ?
            """,
            (role, ticket_id, session, harness, model, worktree_path, status, start_commit,
             now, pid, db.repository_id, agent_id),
        )


__all__ = ["RosterRepository"]
