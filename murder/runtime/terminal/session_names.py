"""Shared tmux session name formatting (orchestrator, TUI usage probes)."""

from __future__ import annotations

from typing import Protocol

from murder.config import Config


class SessionNameScope(Protocol):
    @property
    def config(self) -> Config: ...


def format_session_name(rt: SessionNameScope, role: str, suffix: str) -> str:
    proj = rt.config.project.name.replace(" ", "_").replace("/", "_")
    tpl = rt.config.runtime.session_name_template
    return tpl.format(project=proj, role=role, suffix=suffix)


def project_session_prefix(rt: SessionNameScope) -> str:
    """Prefix shared by this project's murder-owned tmux sessions."""
    return format_session_name(rt, "", "")
