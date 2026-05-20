"""Structured validators for ticket completion (W4)."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol

from murder.enforcement import git_diff
from murder.enforcement.checklist_verify import format_report, verify_checklist

try:
    from enum import StrEnum
except ImportError:  # pragma: no cover - Python 3.10

    class StrEnum(str, Enum):  # type: ignore[no-redef]
        def __str__(self) -> str:
            return str.__str__(self)


class ValidatorOutcome(StrEnum):
    PASS = "pass"
    REPROMPT = "reprompt"
    ESCALATE = "escalate"
    BLOCKED = "blocked"
    MANUAL_TEST_REQUIRED = "manual_test_required"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ValidatorResult:
    outcome: ValidatorOutcome
    message: str
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class CompletionContext:
    ticket_id: str
    write_set: tuple[Path, ...]
    repo_root: Path
    db: sqlite3.Connection
    start_commit: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "write_set", tuple(Path(p) for p in self.write_set))
        object.__setattr__(self, "repo_root", Path(self.repo_root))


class Validator(Protocol):
    async def validate(self, context: CompletionContext) -> ValidatorResult: ...


def _repo_path(repo_root: Path, path: Path) -> Path:
    return (repo_root / path).resolve()


class ArtifactCheckValidator:
    async def validate(self, context: CompletionContext) -> ValidatorResult:
        failures: list[str] = []
        for path in context.write_set:
            target = _repo_path(context.repo_root, path)
            if not target.exists():
                failures.append(f"{path} (missing)")
            elif target.is_file() and target.stat().st_size == 0:
                failures.append(f"{path} (empty)")
            elif target.is_dir() and not any(target.iterdir()):
                failures.append(f"{path} (empty directory)")
        if failures:
            return ValidatorResult(
                ValidatorOutcome.FAILED,
                "write_set artefacts missing or empty",
                ", ".join(failures[:8]),
            )
        return ValidatorResult(ValidatorOutcome.PASS, "write_set artefacts present")


class ChecklistValidator:
    async def validate(self, context: CompletionContext) -> ValidatorResult:
        report = verify_checklist(context.db, context.ticket_id, context.repo_root)
        if report.overall_ok:
            return ValidatorResult(ValidatorOutcome.PASS, "checklist verified")
        return ValidatorResult(
            ValidatorOutcome.FAILED,
            "checklist verification failed",
            format_report(report),
        )


class WriteSetDiffValidator:
    async def validate(self, context: CompletionContext) -> ValidatorResult:
        if not context.start_commit:
            return ValidatorResult(ValidatorOutcome.PASS, "no start commit — skip diff check")
        try:
            dirty = await git_diff.diff_outside(
                context.repo_root, context.start_commit, list(context.write_set)
            )
        except Exception as exc:
            return ValidatorResult(
                ValidatorOutcome.ESCALATE,
                "git diff check failed",
                str(exc),
            )
        if not dirty:
            return ValidatorResult(ValidatorOutcome.PASS, "diff within write_set")
        return ValidatorResult(
            ValidatorOutcome.BLOCKED,
            "diff outside write_set",
            str(dirty[:5]),
        )


class ValidatorPipeline:
    def __init__(self, validators: Sequence[Validator] | None = None) -> None:
        self._validators = list(validators or DEFAULT_VALIDATORS)

    async def run(self, context: CompletionContext) -> list[ValidatorResult]:
        results: list[ValidatorResult] = []
        for validator in self._validators:
            result = await validator.validate(context)
            results.append(result)
            if result.outcome != ValidatorOutcome.PASS:
                break
        return results


DEFAULT_VALIDATORS: list[Validator] = [
    ArtifactCheckValidator(),
    ChecklistValidator(),
    WriteSetDiffValidator(),
]


def policy(results: Sequence[ValidatorResult]) -> ValidatorOutcome:
    """Map validator results to a single completion decision."""
    for result in results:
        if result.outcome != ValidatorOutcome.PASS:
            return result.outcome
    return ValidatorOutcome.PASS


def first_failure_message(results: Sequence[ValidatorResult]) -> str:
    for result in results:
        if result.outcome != ValidatorOutcome.PASS:
            if result.detail:
                return f"{result.message}: {result.detail}"
            return result.message
    return "validation failed"


__all__ = [
    "ArtifactCheckValidator",
    "ChecklistValidator",
    "CompletionContext",
    "DEFAULT_VALIDATORS",
    "Validator",
    "ValidatorOutcome",
    "ValidatorPipeline",
    "ValidatorResult",
    "WriteSetDiffValidator",
    "first_failure_message",
    "policy",
]
