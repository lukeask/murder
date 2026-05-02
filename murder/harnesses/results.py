from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass(slots=True)
class SimpleResult(Generic[T]):
    ok: bool
    message: str | None = None
    data: T | None = None


def ok_result(data: T | None = None, message: str | None = None) -> SimpleResult[T]:
    return SimpleResult(ok=True, message=message, data=data)


def fail_result(message: str) -> SimpleResult[T]:
    return SimpleResult(ok=False, message=message, data=None)
