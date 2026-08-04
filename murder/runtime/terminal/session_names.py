"""Shared tmux session name formatting (orchestrator, TUI usage probes)."""

from __future__ import annotations

from dataclasses import dataclass

from murder.config import Config


@dataclass(frozen=True)
class SessionNamePolicy:
    """Explicit naming policy — avoids passing broad objects merely to derive names."""

    project_name: str
    template: str

    @classmethod
    def from_config(cls, config: Config) -> SessionNamePolicy:
        proj = config.project.name.replace(" ", "_").replace("/", "_")
        return cls(project_name=proj, template=config.runtime.session_name_template)

    def format(self, role: str, suffix: str) -> str:
        return self.template.format(project=self.project_name, role=role, suffix=suffix)

    def project_prefix(self) -> str:
        """Prefix shared by this project's murder-owned tmux sessions."""
        return self.format("", "")


def format_session_name(policy: SessionNamePolicy, role: str, suffix: str) -> str:
    """Format a session name from an explicit policy.

    Callers that previously passed a config-bearing scope should construct
    ``SessionNamePolicy.from_config(config)`` once and reuse it.
    """
    return policy.format(role, suffix)


def project_session_prefix(policy: SessionNamePolicy) -> str:
    """Prefix shared by this project's murder-owned tmux sessions."""
    return policy.project_prefix()


__all__ = [
    "SessionNamePolicy",
    "format_session_name",
    "project_session_prefix",
]
