"""Shared tmux session name formatting (orchestrator, TUI usage probes)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from murder.runtime import Runtime


def format_session_name(rt: Runtime, role: str, suffix: str) -> str:
    proj = rt.config.project.name.replace(" ", "_").replace("/", "_")
    tpl = rt.config.runtime.session_name_template
    return tpl.format(project=proj, role=role, suffix=suffix)
