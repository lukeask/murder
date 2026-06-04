"""Resolution policy — maps (check_name, attempts) to next owner."""

from __future__ import annotations

from enum import Enum

try:
    from enum import StrEnum
except ImportError:  # pragma: no cover - Python 3.10

    class StrEnum(str, Enum):  # type: ignore[no-redef]
        def __str__(self) -> str:
            return str.__str__(self)


class Owner(StrEnum):
    REPROMPT = "reprompt"
    ASK_PLANNER = "ask_planner"
    ASK_USER = "ask_user"
    FAIL_TICKET = "fail_ticket"


def resolution_policy(check_name: str, attempts: int) -> Owner:
    if attempts == 0:
        return Owner.REPROMPT
    if attempts == 1:
        return Owner.ASK_PLANNER
    if attempts == 2:
        return Owner.ASK_USER
    return Owner.FAIL_TICKET


__all__ = ["Owner", "resolution_policy"]
