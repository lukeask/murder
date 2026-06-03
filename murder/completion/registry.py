"""CheckRegistry — maps tickets to their assigned checks."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .checks import Check, ChecklistCheck


class CheckRegistry:
    def assigned_checks(self, ticket: dict[str, Any] | Mapping[str, Any]) -> list[Check]:
        checklist = ticket.get("checklist") or []
        checks: list[Check] = []
        if checklist:
            checks.append(ChecklistCheck())
        return checks


__all__ = ["CheckRegistry"]
