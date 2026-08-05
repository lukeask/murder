"""Phase 7: tmux session name prefix includes a short repository hash."""

from __future__ import annotations

from murder.config import Config, CrowHandlerConfig, HarnessRoleConfig, ProjectConfig, RuntimeConfig
from murder.runtime.terminal.session_names import SessionNamePolicy, short_repository_hash


def _config(name: str = "repo") -> Config:
    role = HarnessRoleConfig(harness="codex")
    return Config(
        project=ProjectConfig(name=name),
        runtime=RuntimeConfig(session_name_template="murder_{project}_{role}{suffix}"),
        collaborator=role,
        default_crow=role,
        crow_handler=CrowHandlerConfig(model="test-model"),
    )


def test_short_repository_hash_is_stable_hex() -> None:
    h = short_repository_hash("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    assert len(h) == 8
    assert h == short_repository_hash("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    assert h != short_repository_hash("ffffffff-bbbb-cccc-dddd-eeeeeeeeeeee")


def test_from_config_without_repository_id_keeps_project_name() -> None:
    policy = SessionNamePolicy.from_config(_config("my project"))
    assert policy.project_name == "my_project"
    assert policy.repository_id is None
    assert policy.format("crow", "_t1") == "murder_my_project_crow_t1"


def test_from_config_appends_repo_hash_for_prefix_uniqueness() -> None:
    rid_a = "11111111-1111-1111-1111-111111111111"
    rid_b = "22222222-2222-2222-2222-222222222222"
    a = SessionNamePolicy.from_config(_config("shared"), repository_id=rid_a)
    b = SessionNamePolicy.from_config(_config("shared"), repository_id=rid_b)
    assert a.project_name == f"shared_{short_repository_hash(rid_a)}"
    assert b.project_name == f"shared_{short_repository_hash(rid_b)}"
    assert a.project_prefix() != b.project_prefix()
    assert a.format("crow", "_t1").startswith(a.project_prefix())
    assert "crow_t1" in a.format("crow", "_t1")
