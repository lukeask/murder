from __future__ import annotations

from pathlib import Path

from murder.state.persistence.schema import get_db, init_db
from murder.state.storage.paths import ticket_md, tickets_dir
from murder.work.tickets.sync import TicketSync, reconcile_ticket_md


def _conn(repo_root: Path):
    db_file = repo_root / ".murder" / "murder.db"
    db_file.parent.mkdir(parents=True, exist_ok=True)
    conn = get_db(db_file)
    init_db(conn)
    return conn


def _insert_ticket(
    conn,
    ticket_id: str,
    *,
    title: str = "Original",
    status: str = "planned",
    harness: str | None = "codex",
    model: str | None = "gpt-5",
    worktree: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO tickets(
            id, title, status, harness, model, worktree, attempts, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, 0, '2026-06-08T00:00:00', '2026-06-08T00:00:00')
        """,
        (ticket_id, title, status, harness, model, worktree),
    )


def test_reconcile_ticket_md_syncs_frontmatter_and_checklist_to_db(repo_root: Path) -> None:
    conn = _conn(repo_root)
    _insert_ticket(conn, "t000", title="Dependency", status="done")
    _insert_ticket(conn, "t001")
    path = ticket_md(repo_root, "t001")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """---
title: Edited title
deps: [t000]
harness: cursor
model: opus
worktree: feature-edited
---
# Notes
body is not structured

# Checklist
[ ] first
[x] second
""",
        encoding="utf-8",
    )

    reconcile_ticket_md(conn=conn, repo_root=repo_root, ticket_id="t001")

    row = conn.execute("SELECT * FROM tickets WHERE id = 't001'").fetchone()
    assert row["title"] == "Edited title"
    assert row["status"] == "planned"
    assert row["harness"] == "cursor"
    assert row["model"] == "opus"
    assert row["worktree"] == "feature-edited"
    assert row["metadata_sync_state"] == "synced"
    assert row["metadata_parse_error"] is None

    deps = conn.execute(
        "SELECT depends_on_id FROM ticket_deps WHERE ticket_id = 't001'"
    ).fetchall()
    assert [row["depends_on_id"] for row in deps] == ["t000"]
    checklist = conn.execute(
        "SELECT ord, text, done, done_at FROM checklist WHERE ticket_id = 't001' ORDER BY ord"
    ).fetchall()
    assert [(row["ord"], row["text"], row["done"]) for row in checklist] == [
        (0, "first", 0),
        (1, "second", 1),
    ]
    assert checklist[0]["done_at"] is None
    assert checklist[1]["done_at"] is not None


def test_reconcile_ticket_md_preserves_done_at_for_existing_done_items(
    repo_root: Path,
) -> None:
    conn = _conn(repo_root)
    _insert_ticket(conn, "t001")
    conn.execute(
        """
        INSERT INTO checklist(ticket_id, ord, text, done, done_at)
        VALUES ('t001', 0, 'keep timestamp', 1, '2026-06-08T01:02:03')
        """
    )
    ticket_md(repo_root, "t001").parent.mkdir(parents=True, exist_ok=True)
    ticket_md(repo_root, "t001").write_text(
        """---
title: Original
deps: []
harness: codex
model: gpt-5
worktree:
---
# Checklist
[x] keep timestamp
""",
        encoding="utf-8",
    )

    reconcile_ticket_md(conn=conn, repo_root=repo_root, ticket_id="t001")

    row = conn.execute(
        "SELECT done, done_at FROM checklist WHERE ticket_id = 't001' AND text = 'keep timestamp'"
    ).fetchone()
    assert row["done"] == 1
    assert row["done_at"] == "2026-06-08T01:02:03"


def test_ticket_sync_seeds_missing_markdown_from_db(repo_root: Path) -> None:
    conn = _conn(repo_root)
    _insert_ticket(conn, "t000", title="Dependency", status="done")
    _insert_ticket(conn, "t001", title="Seed me", harness="cc", model="opus")
    conn.execute("INSERT INTO ticket_deps(ticket_id, depends_on_id) VALUES ('t001', 't000')")
    conn.execute(
        """
        INSERT INTO checklist(ticket_id, ord, text, done, done_at)
        VALUES
            ('t001', 0, 'todo', 0, NULL),
            ('t001', 1, 'done', 1, '2026-06-08T01:02:03')
        """
    )
    assert not ticket_md(repo_root, "t001").exists()

    sync = TicketSync(repo_root, conn)
    sync._materialize_missing_md()

    text = ticket_md(repo_root, "t001").read_text(encoding="utf-8")
    assert "title: Seed me\n" in text
    assert "deps:\n- t000\n" in text
    assert "harness: cc\n" in text
    assert "model: opus\n" in text
    assert "# Checklist\n[ ] todo\n[x] done\n" in text
    row = conn.execute(
        "SELECT metadata_materialized_path FROM tickets WHERE id = 't001'"
    ).fetchone()
    assert row["metadata_materialized_path"] == ".murder/tickets/t001.md"


def test_ticket_sync_recreates_deleted_markdown_for_single_ticket(repo_root: Path) -> None:
    conn = _conn(repo_root)
    _insert_ticket(conn, "t001", title="Deleted")
    tickets_dir(repo_root).mkdir(parents=True, exist_ok=True)

    reconcile_ticket_md(conn=conn, repo_root=repo_root, ticket_id="t001")

    assert ticket_md(repo_root, "t001").exists()


def test_reconcile_ticket_md_round_trips_parent(repo_root: Path) -> None:
    # Root-cause test: an .md carrying `parent: tNNN` must set (and on every
    # re-reconcile preserve) the `parent_ticket_id` column, not get clobbered.
    conn = _conn(repo_root)
    path = ticket_md(repo_root, "t002")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """---
title: Child
deps: []
harness: codex
model: gpt-5
worktree:
parent: t003
---
# Checklist
[ ] do thing
""",
        encoding="utf-8",
    )

    reconcile_ticket_md(conn=conn, repo_root=repo_root, ticket_id="t002")
    row = conn.execute("SELECT parent_ticket_id FROM tickets WHERE id = 't002'").fetchone()
    assert row["parent_ticket_id"] == "t003"

    # Re-reconcile (the poll path that previously clobbered linkage) keeps it.
    reconcile_ticket_md(conn=conn, repo_root=repo_root, ticket_id="t002")
    row = conn.execute("SELECT parent_ticket_id FROM tickets WHERE id = 't002'").fetchone()
    assert row["parent_ticket_id"] == "t003"


def test_render_row_emits_parent_from_db_column(repo_root: Path) -> None:
    conn = _conn(repo_root)
    _insert_ticket(conn, "t002", title="Child")
    conn.execute("UPDATE tickets SET parent_ticket_id = 't003' WHERE id = 't002'")
    tickets_dir(repo_root).mkdir(parents=True, exist_ok=True)

    # Delete-then-recreate materializes the .md from the DB row.
    reconcile_ticket_md(conn=conn, repo_root=repo_root, ticket_id="t002")

    text = ticket_md(repo_root, "t002").read_text(encoding="utf-8")
    assert "parent: t003\n" in text
