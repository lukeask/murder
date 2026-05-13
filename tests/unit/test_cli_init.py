from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from murder import cli


def test_init_scaffolds_murder_dir_and_gitignore(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    result = runner.invoke(cli.app, ["init"])

    assert result.exit_code == 0, result.output
    assert (tmp_path / ".murder" / "murder.db").exists()
    roles_text = (tmp_path / ".murder" / "roles.yaml").read_text(encoding="utf-8")
    assert f"name: '{tmp_path.name}'" in roles_text
    assert ".murder/" in (tmp_path / ".gitignore").read_text(encoding="utf-8")


def test_bare_command_prompts_to_initialize(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    def fake_run_async_entry(coro) -> None:  # type: ignore[no-untyped-def]
        coro.close()

    monkeypatch.setattr(cli, "_run_async_entry", fake_run_async_entry)

    result = runner.invoke(cli.app, [], input="y\n")

    assert result.exit_code == 0, result.output
    assert "Run `murder init` now?" in result.output
    assert (tmp_path / ".murder" / "murder.db").exists()
