"""Phase 6 architecture acceptance for Runtime god-class decomposition.

Pins final acceptance criteria from
``.murder/reports/murder_runtime_decomposition_spec.md`` §11 and §9 architecture
rules. Characterization that lived on the deleted Runtime facade now lives on
ProcessScope / AgentRuntime / SessionService / ServiceHost / FilesystemSyncService.
"""

from __future__ import annotations

from pathlib import Path


def test_runtime_facade_deleted() -> None:
    repo = Path(__file__).resolve().parents[2]
    assert not (repo / "murder/app/service/runtime.py").exists()
    assert not (repo / "murder/app/service/runtime_scope.py").exists()
    assert not (repo / "murder/runtime/orchestration/runtime_scope.py").exists()
    assert not (repo / "murder/app/service/terminal_capture.py").exists()
    assert not (repo / "murder/app/service/document_access.py").exists()
    assert not (repo / "murder/app/service/document_editor_sessions.py").exists()


def test_phase2_runtime_scope_protocols_deleted() -> None:
    """OrchestratorHost / AgentLifecycleHost are gone."""
    repo = Path(__file__).resolve().parents[2]
    runtime_root = repo / "murder" / "runtime"
    offenders: list[str] = []
    for path in runtime_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "OrchestratorHost" in text or "AgentLifecycleHost" in text:
            offenders.append(str(path.relative_to(repo)))
    assert offenders == [], f"protocol leftovers: {offenders}"


def test_no_production_import_of_app_service_runtime() -> None:
    repo = Path(__file__).resolve().parents[2]
    offenders: list[str] = []
    for path in (repo / "murder").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "murder.app.service.runtime" in text and "runtime_lifecycle" not in text:
            # Allow comments mentioning the deleted module only if no import.
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
                    continue
                if "murder.app.service.runtime" in line and "runtime_lifecycle" not in line:
                    offenders.append(f"{path.relative_to(repo)}: {stripped}")
    assert offenders == [], f"production Runtime imports remain:\n" + "\n".join(offenders)


def test_no_runtime_parameter_in_murder_runtime_package() -> None:
    """No production class under murder/runtime accepts concrete app Runtime."""
    repo = Path(__file__).resolve().parents[2]
    offenders: list[str] = []
    for path in (repo / "murder" / "runtime").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "murder.app.service.runtime" in text or "from murder.app.service import runtime" in text:
            offenders.append(str(path.relative_to(repo)))
    assert offenders == []


def test_no_effects_object_or_structural_casts_in_handlers() -> None:
    repo = Path(__file__).resolve().parents[2]
    handlers = repo / "murder" / "app" / "service" / "handlers"
    offenders: list[str] = []
    for path in handlers.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "effects: object" in text:
            offenders.append(f"{path.relative_to(repo)}: effects: object")
        if "cast(" in text and "Effects" in text:
            offenders.append(f"{path.relative_to(repo)}: cast(...Effects)")
    assert offenders == []


def test_no_filesystem_sync_supervisor_alias() -> None:
    repo = Path(__file__).resolve().parents[2]
    text = (repo / "murder/app/service/filesystem_sync.py").read_text(encoding="utf-8")
    assert "FilesystemSyncSupervisor" not in text


def test_document_service_and_editor_modules_exist() -> None:
    repo = Path(__file__).resolve().parents[2]
    docs = (repo / "murder/app/service/documents.py").read_text(encoding="utf-8")
    editors = (repo / "murder/app/service/document_editors.py").read_text(encoding="utf-8")
    assert "class DocumentService" in docs
    assert "DocumentTarget" in docs
    assert "class DocumentEditorService" in editors
    assert "update_documents" not in editors
    assert "def capture" not in editors
    assert "def send(" not in editors
    # §11: started DocumentService has no nullable DB/sync fields.
    assert "db: RepoDb | None" not in docs
    assert "plan_sync: PlanSync | None" not in docs
    assert "note_sync:" in docs and " | None" not in docs.split("note_sync:")[1].split("\n")[0]


def test_harness_control_routes_revival_through_session_service_only() -> None:
    """§11: no duplicate revival orchestration beside SessionService."""
    repo = Path(__file__).resolve().parents[2]
    text = (
        repo / "murder/llm/harness_control/runtime/session.py"
    ).read_text(encoding="utf-8")
    assert "persist_or_revive_tmux_session" not in text
    assert "ensure_persisted_tmux_session" in text
    assert 'raise ValueError(\n                "SessionService is required' in text or (
        "SessionService is required when creating a session controller" in text
    )

def test_off_protocol_seams_threaded_onto_explicit_deps() -> None:
    """Former off-protocol Runtime attributes remain on explicit deps."""
    repo = Path(__file__).resolve().parents[2]
    base = (repo / "murder/runtime/agents/base.py").read_text(encoding="utf-8")
    assert "heartbeat(" in base
    crow = (repo / "murder/runtime/agents/crow_handler.py").read_text(encoding="utf-8")
    assert "crow_ask_router" in crow
    assert ".heartbeat(" in crow
    plan = (repo / "murder/runtime/orchestration/plan_ops.py").read_text(encoding="utf-8")
    assert "_plan_sync" in plan
    assert "_user_config" in plan


def test_handlers_do_not_receive_service_host() -> None:
    """§2.1 / §11: handlers receive feature operations, not ServiceHost."""
    repo = Path(__file__).resolve().parents[2]
    handlers = repo / "murder" / "app" / "service" / "handlers"
    offenders: list[str] = []
    for path in handlers.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "legacy_host" in text:
            offenders.append(f"{path.relative_to(repo)}: legacy_host")
        if "from murder.app.service.host import ServiceHost" in text:
            offenders.append(f"{path.relative_to(repo)}: ServiceHost import")
    assert offenders == [], f"ServiceHost handler coupling remains:\n" + "\n".join(offenders)


def test_agent_runtime_constructs_process_bindings() -> None:
    """AgentRuntime process bindings are constructor deps, not post-open mutation."""
    repo = Path(__file__).resolve().parents[2]
    host = (repo / "murder/app/service/host.py").read_text(encoding="utf-8")
    runtime = (repo / "murder/runtime/agent_runtime.py").read_text(encoding="utf-8")
    assert "agents.command_submitter =" not in host
    assert "agents.sessions =" not in host
    assert "command_submitter=process.commands" in host
    assert "sessions=sessions" in host
    assert "class CrowAskRouterSlot" in runtime
    assert "agents.crow_ask_router.bind(" in host
    assert "self.crow_ask_router: CrowAskRouter | None = None" not in runtime