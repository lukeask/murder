"""ArtifactCheck — verifies write_set files exist and are non-empty."""

from __future__ import annotations

from pathlib import Path

from .base import Check, CheckResult, CheckStatus, CompletionContext


def _repo_path(repo_root: Path, path: Path) -> Path:
    return (repo_root / path).resolve()


def write_set_artifacts_present(repo_root: Path, write_set: list[Path]) -> bool:
    """True when every write_set path exists and is non-empty."""
    for path in write_set:
        target = _repo_path(repo_root, path)
        if not target.exists():
            return False
        if target.is_file() and target.stat().st_size == 0:
            return False
        if target.is_dir() and not any(target.iterdir()):
            return False
    return True


class ArtifactCheck:
    name = "artifact"

    async def run(self, ctx: CompletionContext) -> CheckResult:
        if write_set_artifacts_present(ctx.repo_root, list(ctx.write_set)):
            return CheckResult(CheckStatus.PASS, message="write_set artefacts present")
        failures: list[str] = []
        for path in ctx.write_set:
            target = _repo_path(ctx.repo_root, path)
            if not target.exists():
                failures.append(f"{path} (missing)")
            elif target.is_file() and target.stat().st_size == 0:
                failures.append(f"{path} (empty)")
            elif target.is_dir() and not any(target.iterdir()):
                failures.append(f"{path} (empty directory)")
        if failures:
            detail = ", ".join(failures[:8])
            return CheckResult(
                CheckStatus.FAIL,
                message="write_set artefacts missing or empty",
                hint=f"Create or populate the following files: {detail}",
            )
        return CheckResult(CheckStatus.PASS, message="write_set artefacts present")


__all__ = ["ArtifactCheck", "write_set_artifacts_present"]
