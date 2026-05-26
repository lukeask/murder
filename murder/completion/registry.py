"""CheckRegistry — maps tickets to their assigned checks."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .checks import ArtifactCheck, Check, ChecklistCheck, WriteSetCheck


class CheckRegistry:
    def assigned_checks(self, ticket: dict[str, Any] | Mapping[str, Any]) -> list[Check]:
        write_set = ticket.get("write_set") or []
        checklist = ticket.get("checklist") or []

        checks: list[Check] = []
        if write_set:
            checks.append(ArtifactCheck())
            checks.append(WriteSetCheck())
        if checklist:
            checks.append(ChecklistCheck())
        return checks


__all__ = ["CheckRegistry"]
