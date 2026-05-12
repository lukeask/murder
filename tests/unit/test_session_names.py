"""Session name helper used by orchestrator and usage probes."""

from unittest.mock import MagicMock

from murder.session_names import format_session_name


def test_format_session_name_sanitizes_project_and_formats_template() -> None:
    rt = MagicMock()
    rt.config.project.name = "my proj/name"
    rt.config.runtime.session_name_template = "murder_{project}_{role}{suffix}"
    assert (
        format_session_name(rt, "usage", "_claude_code") == "murder_my_proj_name_usage_claude_code"
    )
