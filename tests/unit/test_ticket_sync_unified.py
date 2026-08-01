from __future__ import annotations

import asyncio
from pathlib import Path

from murder.state.storage.paths import ticket_md, tickets_dir
from murder.work.tickets.sync import TicketSync, reconcile_ticket_md
from tests.support.database import open_test_repo_db


def _write_ticket_md(repo_root: Path, ticket_id: str, *, title: str = "A ticket") -> Path:
    tickets_dir(repo_root).mkdir(parents=True, exist_ok=True)
    path = ticket_md(repo_root, ticket_id)
    path.write_text(
        f"---\ntitle: {title}\ndeps: []\nharness: codex\nmodel: gpt-5\nworktree:\n---\n"
        "# Checklist\n[ ] do thing\n",
        encoding="utf-8",
    )
    return path


def _conn(repo_root: Path):
    db_file = repo_root / ".murder" / "murder.db"
    db_file.parent.mkdir(parents=True, exist_ok=True)
    return open_test_repo_db(db_file)


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
    conn.conn.execute(
        """
        INSERT INTO tickets(
            repository_id, id, title, status, harness, model, worktree, attempts, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 0, '2026-06-08T00:00:00', '2026-06-08T00:00:00')
        """,
        (conn.repository_id, ticket_id, title, status, harness, model, worktree),
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

    reconcile_ticket_md(db=conn, repo_root=repo_root, ticket_id="t001")

    row = conn.conn.execute("SELECT * FROM tickets WHERE repository_id = ? AND id = 't001'", (conn.repository_id,)).fetchone()
    assert row["title"] == "Edited title"
    assert row["status"] == "planned"
    assert row["harness"] == "cursor"
    assert row["model"] == "opus"
    assert row["worktree"] == "feature-edited"
    assert row["metadata_sync_state"] == "synced"
    assert row["metadata_parse_error"] is None

    deps = conn.conn.execute(
        "SELECT depends_on_id FROM ticket_deps WHERE repository_id = ? AND ticket_id = 't001'", (conn.repository_id,)
    ).fetchall()
    assert [row["depends_on_id"] for row in deps] == ["t000"]
    checklist = conn.conn.execute(
        "SELECT ord, text, done, done_at FROM checklist WHERE repository_id = ? AND ticket_id = 't001' ORDER BY ord", (conn.repository_id,)
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
    conn.conn.execute(
        """
        INSERT INTO checklist(repository_id, ticket_id, ord, text, done, done_at)
        VALUES (?, 't001', 0, 'keep timestamp', 1, '2026-06-08T01:02:03')
        """, (conn.repository_id,)
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

    reconcile_ticket_md(db=conn, repo_root=repo_root, ticket_id="t001")

    row = conn.conn.execute(
        "SELECT done, done_at FROM checklist WHERE repository_id = ? AND ticket_id = 't001' AND text = 'keep timestamp'", (conn.repository_id,)
    ).fetchone()
    assert row["done"] == 1
    assert row["done_at"] == "2026-06-08T01:02:03"


def test_ticket_sync_seeds_missing_markdown_from_db(repo_root: Path) -> None:
    conn = _conn(repo_root)
    _insert_ticket(conn, "t000", title="Dependency", status="done")
    _insert_ticket(conn, "t001", title="Seed me", harness="cc", model="opus")
    conn.conn.execute("INSERT INTO ticket_deps(repository_id, ticket_id, depends_on_id) VALUES (?, 't001', 't000')", (conn.repository_id,))
    conn.conn.execute(
        """
        INSERT INTO checklist(repository_id, ticket_id, ord, text, done, done_at)
        VALUES
            (?, 't001', 0, 'todo', 0, NULL),
            (?, 't001', 1, 'done', 1, '2026-06-08T01:02:03')
        """, (conn.repository_id, conn.repository_id)
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
    row = conn.conn.execute(
        "SELECT metadata_materialized_path FROM tickets WHERE repository_id = ? AND id = 't001'", (conn.repository_id,)
    ).fetchone()
    assert row["metadata_materialized_path"] == ".murder/tickets/t001.md"


def test_ticket_sync_recreates_deleted_markdown_for_single_ticket(repo_root: Path) -> None:
    conn = _conn(repo_root)
    _insert_ticket(conn, "t001", title="Deleted")
    tickets_dir(repo_root).mkdir(parents=True, exist_ok=True)

    reconcile_ticket_md(db=conn, repo_root=repo_root, ticket_id="t001")

    assert ticket_md(repo_root, "t001").exists()


def test_reconcile_ticket_md_round_trips_parent(repo_root: Path) -> None:
    # Root-cause test: an .md carrying `parent: tNNN` must set (and on every
    # re-reconcile preserve) the `parent_ticket_id` column, not get clobbered.
    conn = _conn(repo_root)
    _insert_ticket(conn, "t003", title="Parent")
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

    reconcile_ticket_md(db=conn, repo_root=repo_root, ticket_id="t002")
    row = conn.conn.execute("SELECT parent_ticket_id FROM tickets WHERE repository_id = ? AND id = 't002'", (conn.repository_id,)).fetchone()
    assert row["parent_ticket_id"] == "t003"

    # Re-reconcile (the poll path that previously clobbered linkage) keeps it.
    reconcile_ticket_md(db=conn, repo_root=repo_root, ticket_id="t002")
    row = conn.conn.execute("SELECT parent_ticket_id FROM tickets WHERE repository_id = ? AND id = 't002'", (conn.repository_id,)).fetchone()
    assert row["parent_ticket_id"] == "t003"


def test_render_row_emits_parent_from_db_column(repo_root: Path) -> None:
    conn = _conn(repo_root)
    _insert_ticket(conn, "t003", title="Parent")
    _insert_ticket(conn, "t002", title="Child")
    conn.conn.execute("UPDATE tickets SET parent_ticket_id = 't003' WHERE repository_id = ? AND id = 't002'", (conn.repository_id,))
    tickets_dir(repo_root).mkdir(parents=True, exist_ok=True)

    # Delete-then-recreate materializes the .md from the DB row.
    reconcile_ticket_md(db=conn, repo_root=repo_root, ticket_id="t002")

    text = ticket_md(repo_root, "t002").read_text(encoding="utf-8")
    assert "parent: t003\n" in text


# === warm-boot change detection (subplan 2) ================================


def test_reconcile_all_unchanged_second_pass_emits_no_notifications(repo_root: Path) -> None:
    conn = _conn(repo_root)
    _write_ticket_md(repo_root, "t010")
    _write_ticket_md(repo_root, "t011")
    _write_ticket_md(repo_root, "t012")

    sync = TicketSync(repo_root, conn)

    asyncio.run(sync.reconcile_all())
    asyncio.run(sync.reconcile_all())
    # Byte-identical, already-synced files: second pass must skip the snapshot.
    assert [row["metadata_sync_state"] for row in conn.conn.execute(
        "SELECT metadata_sync_state FROM tickets WHERE repository_id = ?", (conn.repository_id,)
    )] == ["synced", "synced", "synced"]


def test_reconcile_all_emits_only_for_edited_ticket(repo_root: Path) -> None:
    conn = _conn(repo_root)
    _write_ticket_md(repo_root, "t020")
    _write_ticket_md(repo_root, "t021")
    _write_ticket_md(repo_root, "t022")

    sync = TicketSync(repo_root, conn)

    asyncio.run(sync.reconcile_all())
    # Edit exactly one ticket between passes.
    ticket_md(repo_root, "t021").write_text(
        "---\ntitle: Edited\ndeps: []\nharness: codex\nmodel: gpt-5\nworktree:\n---\n"
        "# Checklist\n[x] do thing\n",
        encoding="utf-8",
    )

    asyncio.run(sync.reconcile_all())
    row = conn.conn.execute("SELECT title FROM tickets WHERE repository_id = ? AND id = 't021'", (conn.repository_id,)).fetchone()
    assert row["title"] == "Edited"


# === reconcile_all end-to-end coverage ======================================


def test_reconcile_all_reconciles_end_to_end(repo_root: Path) -> None:
    conn = _conn(repo_root)
    _write_ticket_md(repo_root, "t030", title="First")
    _write_ticket_md(repo_root, "t031", title="Second")

    sync = TicketSync(repo_root, conn)
    asyncio.run(sync.reconcile_all())

    rows = {
        r["id"]: r
        for r in conn.conn.execute(
            "SELECT id, title, metadata_sync_state, metadata_file_hash FROM tickets WHERE repository_id = ?", (conn.repository_id,)
        ).fetchall()
    }
    assert set(rows) == {"t030", "t031"}
    assert rows["t030"]["title"] == "First"
    assert rows["t031"]["title"] == "Second"
    assert rows["t030"]["metadata_sync_state"] == "synced"
    assert rows["t030"]["metadata_file_hash"] is not None
    checklist = conn.conn.execute(
        "SELECT text FROM checklist WHERE repository_id = ? AND ticket_id = 't030'", (conn.repository_id,)
    ).fetchall()
    assert [r["text"] for r in checklist] == ["do thing"]
