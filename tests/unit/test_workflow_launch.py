"""Launch a saved workflow by name: registry lookup + materialize.

The logic under test lives in ``run_workflow_by_name`` (name -> definition ->
materialize); the ``tui.run_workflow`` handler is a thin shell over it. We test
the extracted function thoroughly here and cover the handler's input-validation
shell (empty name, non-dict args, unknown name) against an unstarted host —
the happy-path handler test (publish + kickoff) needs a live runtime+orchestrator
and is left to integration, since no unit test in this suite starts a full
Runtime and a stub harness would just re-assert the materialize behavior already
covered below.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from murder.app.service.host import ServiceHost
from murder.config import (
    Config,
    CrowHandlerConfig,
    HarnessRoleConfig,
    ProjectConfig,
)
from murder.state.persistence.schema import get_db, init_db
from murder.user_config import save_workflows
from murder.work.workflows.launch import run_workflow_by_name
from murder.work.workflows.materialize import MaterializeResult


@pytest.fixture
def xdg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home))
    return home


def _conn(repo_root: Path):
    db_file = repo_root / ".murder" / "murder.db"
    db_file.parent.mkdir(parents=True, exist_ok=True)
    conn = get_db(db_file)
    init_db(conn)
    return conn


def _two_stage_workflow(name: str) -> dict:
    """A valid 2-stage workflow (scout -> plan) with harness+model per stage."""
    return {
        "name": name,
        "description": "scout then plan",
        "stages": [
            {
                "id": "scout",
                "title": "Scout the codebase",
                "instructions": "Survey the area.",
                "harness": "codex",
                "model": "gpt-5",
            },
            {
                "id": "plan",
                "title": "Plan follow-ups",
                "instructions": "Plan for spec: {spec}",
                "harness": "codex",
                "model": "gpt-5",
                "depends_on": ["scout"],
            },
        ],
    }


def _host(repo_root: Path) -> ServiceHost:
    config = Config(
        project=ProjectConfig(name="repo"),
        collaborator=HarnessRoleConfig(harness="codex"),
        default_crow=HarnessRoleConfig(harness="codex"),
        crow_handler=CrowHandlerConfig(model="test-model"),
    )
    host = ServiceHost(config=config, repo_root=repo_root)
    host.register_default_rpc_handlers()
    return host


# --- run_workflow_by_name ------------------------------------------------


def test_run_by_name_materializes_saved_workflow(repo_root: Path, xdg: Path) -> None:
    save_workflows([_two_stage_workflow("thename")])
    conn = _conn(repo_root)

    result = run_workflow_by_name(conn, repo_root, "thename", {"spec": "do it"})

    assert isinstance(result, MaterializeResult)
    assert set(result.stage_ticket_ids) == {"scout", "plan"}

    parent = conn.execute(
        "SELECT status FROM tickets WHERE id = ?", (result.run_ticket_id,)
    ).fetchone()
    assert parent is not None
    assert parent["status"] == "planned"

    for ticket_id in result.stage_ticket_ids.values():
        row = conn.execute(
            "SELECT status, parent_ticket_id FROM tickets WHERE id = ?", (ticket_id,)
        ).fetchone()
        assert row is not None
        assert row["status"] == "ready"
        assert row["parent_ticket_id"] == result.run_ticket_id


def test_run_by_name_last_dupe_wins(
    repo_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # save normalizes dupes away, but the launcher must still pick the LAST
    # definition if a registry ever carried two with the same name. We stub the
    # registry read to feed a genuine duplicate and prove the last is chosen by
    # its distinguishing stage title.
    first = _two_stage_workflow("dup")
    second = _two_stage_workflow("dup")
    second["stages"][0]["title"] = "WINNER"
    monkeypatch.setattr(
        "murder.user_config.load_workflows", lambda: [first, second]
    )
    conn = _conn(repo_root)

    result = run_workflow_by_name(conn, repo_root, "dup", {"spec": "x"})

    from murder.state.storage.paths import ticket_md

    scout_md = ticket_md(repo_root, result.stage_ticket_ids["scout"]).read_text(
        encoding="utf-8"
    )
    assert "WINNER" in scout_md


def test_run_by_name_unknown_raises_keyerror(repo_root: Path, xdg: Path) -> None:
    save_workflows([_two_stage_workflow("thename")])
    conn = _conn(repo_root)
    with pytest.raises(KeyError):
        run_workflow_by_name(conn, repo_root, "missing", {})


# --- tui.run_workflow handler input guards -------------------------------


def _handler(host: ServiceHost):
    return host._rpc_handlers["tui.run_workflow"]


def test_handler_empty_name_rejected(repo_root: Path, xdg: Path) -> None:
    import asyncio

    host = _host(repo_root)
    with pytest.raises(ValueError):
        asyncio.run(_handler(host)({"name": "  "}))


def test_handler_non_dict_args_rejected(repo_root: Path, xdg: Path) -> None:
    import asyncio

    host = _host(repo_root)
    with pytest.raises(ValueError):
        asyncio.run(_handler(host)({"name": "thename", "args": "nope"}))


def test_handler_unstarted_runtime_rejected(repo_root: Path, xdg: Path) -> None:
    # With valid input but no started runtime/orchestrator, the handler refuses
    # rather than touching a None db. (The unknown-name -> ValueError mapping and
    # the publish/kickoff happy path require a live runtime+orchestrator and are
    # covered at the integration layer; run_workflow_by_name's KeyError contract
    # is asserted directly above.)
    import asyncio

    save_workflows([_two_stage_workflow("thename")])
    host = _host(repo_root)
    with pytest.raises(RuntimeError):
        asyncio.run(_handler(host)({"name": "thename", "args": {"spec": "x"}}))
