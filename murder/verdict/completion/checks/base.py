"""Base types for completion checks."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol

try:
    from enum import StrEnum
except ImportError:  # pragma: no cover - Python 3.10

    class StrEnum(str, Enum):  # type: ignore[no-redef]
        def __str__(self) -> str:
            return str.__str__(self)


class CheckStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"


@dataclass(frozen=True)
class CheckResult:
    status: CheckStatus
    message: str = ""
    hint: str = ""


@dataclass(frozen=True, slots=True)
class CompletionContext:
    ticket_id: str
    repo_root: Path
    db: sqlite3.Connection

    def __post_init__(self) -> None:
        object.__setattr__(self, "repo_root", Path(self.repo_root))


class Check(Protocol):
    name: str

    async def run(self, ctx: CompletionContext) -> CheckResult: ...


__all__ = [
    "Check",
    "CheckResult",
    "CheckStatus",
    "CompletionContext",
]
