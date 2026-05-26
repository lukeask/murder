"""ArtifactCheck — verifies write_set files exist and are non-empty."""

from __future__ import annotations

from pathlib import Path

from .base import Check, CheckResult, CheckStatus, CompletionContext


def _repo_path(repo_root: Path, path: Path) -> Path:
    return (repo_root / path).resolve()


class ArtifactCheck:
    name = "artifact"

    async def run(self, ctx: CompletionContext) -> CheckResult:
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


__all__ = ["ArtifactCheck"]
