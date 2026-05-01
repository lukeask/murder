"""Config loader: bundled defaults + project override + .env."""

from __future__ import annotations

import pytest


def test_default_tui_refresh_is_1000ms() -> None:
    """D11."""
    from murder.config import TuiConfig

    assert TuiConfig().refresh_ms == 1000


def test_load_with_no_project_yaml_uses_bundled_defaults(tmp_path) -> None:
    # TODO(M0): create tmp_path/.agents/; do not write roles.yaml; Config.load(tmp_path).
    pytest.skip("M0 stub")


def test_invalid_yaml_fails_loud(tmp_path) -> None:
    # TODO(M0): write malformed roles.yaml; Config.load raises with field path.
    pytest.skip("M0 stub")
