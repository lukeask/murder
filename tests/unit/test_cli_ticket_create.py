from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from murder import db as dbmod
from murder.cli import app


def _ticket_row(repo: Path, ticket_id: str) -> dict[str, object]:
    conn = dbmod.connect(repo / ".murder" / "murder.db")
    try:
        row = dbmod.get_ticket(conn, ticket_id)
    finally:
        conn.close()
    assert row is not None
    return row


def test_ticket_create_from_cli_options(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    init = runner.invoke(app, ["init"])
    assert init.exit_code == 0

    result = runner.invoke(
        app,
        [
            "ticket",
            "create",
            "t001",
            "First ticket",
            "--wave",
            "1",
            "--status",
            "ready",
            "--plan",
            "Wire up the dogfood path.",
            "--write",
            "murder/cli.py",
            "--check",
            "Create the row",
            "--check",
            "Write markdown",
            "--skill",
            "python",
            "--harness",
            "codex",
            "--model",
            "gpt-5",
        ],
    )

    assert result.exit_code == 0, result.output
    row = _ticket_row(tmp_path, "t001")
    assert row["title"] == "First ticket"
    assert row["wave"] == 1
    assert row["status"] == "ready"
    assert row["write_set"] == ["murder/cli.py"]
    assert row["skills"] == ["python"]
    assert row["harness"] == "codex"
    assert row["model"] == "gpt-5"
    assert [item["text"] for item in row["checklist"]] == [
        "Create the row",
        "Write markdown",
    ]
    assert "Wire up the dogfood path." in (
        tmp_path / ".murder" / "tickets" / "t001.md"
    ).read_text(encoding="utf-8")


def test_ticket_create_imports_markdown_sections(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    assert runner.invoke(app, ["init"]).exit_code == 0
    source = tmp_path / "ticket.md"
    source.write_text(
        "\n".join(
            [
                "## Plan",
                "Imported plan.",
                "",
                "## Working notes",
                "Imported notes.",
                "",
                "## Sentinel notes",
                "Imported sentinel notes.",
                "",
            ]
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        ["ticket", "create", "t002", "Imported ticket", "--from", str(source)],
    )

    assert result.exit_code == 0, result.output
    row = _ticket_row(tmp_path, "t002")
    assert row["title"] == "Imported ticket"
    body = (tmp_path / ".murder" / "tickets" / "t002.md").read_text(encoding="utf-8")
    assert "Imported plan." in body
    assert "Imported notes." in body
    assert "Imported sentinel notes." in body


def test_ticket_create_requires_initialized_db(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    result = runner.invoke(app, ["ticket", "create", "t003", "No DB"])

    assert result.exit_code == 1
    assert "No murder.db" in result.output
    assert not (tmp_path / ".murder" / "tickets" / "t003.md").exists()


def test_lint_allows_future_write_set_files_before_done(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    assert runner.invoke(app, ["init"]).exit_code == 0
    created = runner.invoke(
        app,
        [
            "ticket",
            "create",
            "t004",
            "Create missing files later",
            "--status",
            "ready",
            "--write",
            "index.html",
        ],
    )
    assert created.exit_code == 0, created.output

    result = runner.invoke(app, ["lint"])
    assert result.exit_code == 0, result.output


def test_lint_reports_missing_done_write_set(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    assert runner.invoke(app, ["init"]).exit_code == 0
    created = runner.invoke(
        app,
        [
            "ticket",
            "create",
            "t005",
            "Claimed complete",
            "--status",
            "done",
            "--write",
            "index.html",
        ],
    )
    assert created.exit_code == 0, created.output

    result = runner.invoke(app, ["lint"])
    assert result.exit_code == 1
    assert "done ticket write_set path missing: index.html" in result.output


def test_lint_imports_orphan_plan_markdown(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    assert runner.invoke(app, ["init"]).exit_code == 0
    plan_path = tmp_path / ".murder" / "plans" / "hello-web.md"
    plan_path.write_text(
        """---
name: hello-web
status: accepted
created_at: '2026-05-04T20:00:00'
---
# Hello web
""",
        encoding="utf-8",
    )

    lint = runner.invoke(app, ["lint"])
    assert lint.exit_code == 0, lint.output

    conn = dbmod.connect(tmp_path / ".murder" / "murder.db")
    try:
        row = dbmod.get_plan_row(conn, "hello-web")
    finally:
        conn.close()
    assert row is not None
