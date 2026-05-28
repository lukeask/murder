"""Probe installed harness CLI versions via subprocess --version flags.

This module is the only place that shells out to harness binaries for version
detection.  It is used by ``HarnessVersionProbeWorker`` (Track B service) and
will also be used by ``tools/testing/check_harness_versions.py`` (Track A dev
gate, Phase 2).

Each probe is independent: failure or timeout for one kind does not block
others.  The result is ``None`` when the version cannot be determined (binary
absent, error exit, or timeout).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

_log = logging.getLogger(__name__)

# Default binary names and --version argv per harness kind.
# ``cursor --version`` exits non-zero when the IDE is not installed, so the
# probe will legitimately return None on machines without Cursor.
_VERSION_ARGV: dict[str, list[str]] = {
    "claude_code": ["claude", "--version"],
    "codex": ["codex", "--version"],
    "cursor": ["cursor", "--version"],
    "pi": ["pi", "--version"],
    "antigravity": ["agy", "--version"],
    # native_coding_crow has no external CLI — never probe it
}

_DEFAULT_TIMEOUT_S = 8.0


@dataclass(frozen=True, slots=True)
class ProbeResult:
    kind: str
    raw: str | None
    binary_used: str


async def probe_harness_version(
    kind: str,
    *,
    binary_override: str | None = None,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
) -> ProbeResult:
    """Run ``<binary> --version`` for *kind* and return the raw output string.

    Returns ``ProbeResult(kind, raw=None, ...)`` on timeout, missing binary,
    or non-zero exit.  Never raises.
    """
    argv = list(_VERSION_ARGV.get(kind, []))
    if not argv:
        return ProbeResult(kind=kind, raw=None, binary_used="")

    if binary_override:
        argv[0] = binary_override
    binary = argv[0]

    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        except asyncio.TimeoutError:
            proc.kill()
            with asyncio.timeout(2.0):
                await proc.wait()
            _log.warning("probe %s: timed out after %.1fs", kind, timeout_s)
            return ProbeResult(kind=kind, raw=None, binary_used=binary)

        if proc.returncode != 0:
            _log.debug("probe %s: exit %s (version unavailable)", kind, proc.returncode)
            return ProbeResult(kind=kind, raw=None, binary_used=binary)

        # Some binaries (e.g. pi) write their version to stderr instead of stdout.
        raw = stdout.decode(errors="replace").strip() or stderr.decode(errors="replace").strip()
        return ProbeResult(kind=kind, raw=raw or None, binary_used=binary)

    except FileNotFoundError:
        _log.debug("probe %s: binary %r not found on PATH", kind, binary)
        return ProbeResult(kind=kind, raw=None, binary_used=binary)
    except OSError as exc:
        _log.warning("probe %s: OS error: %s", kind, exc)
        return ProbeResult(kind=kind, raw=None, binary_used=binary)


async def probe_all(
    kinds: list[str],
    binary_overrides: dict[str, str] | None = None,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
) -> list[ProbeResult]:
    """Probe all *kinds* concurrently; results are in the same order as input."""
    overrides = binary_overrides or {}
    tasks = [
        probe_harness_version(kind, binary_override=overrides.get(kind), timeout_s=timeout_s)
        for kind in kinds
    ]
    return list(await asyncio.gather(*tasks))


def probeable_kinds() -> list[str]:
    """Harness kinds that have a --version command defined."""
    return list(_VERSION_ARGV)


def binary_overrides_from_config(config: object) -> dict[str, str]:
    """Extract ``HarnessRoleConfig.binary`` overrides from a Config object.

    Returns a mapping of ``kind → binary_path`` for any role that specifies a
    non-default binary.  Crow pool takes precedence over collaborator when the
    same kind appears in both.
    """
    overrides: dict[str, str] = {}
    try:
        collab = config.collaborator  # type: ignore[union-attr]
        if collab.binary:
            overrides[collab.harness] = collab.binary
    except AttributeError:
        pass
    try:
        crow_cfg = config.default_crow  # type: ignore[union-attr]
        if crow_cfg.binary:
            pool = list(crow_cfg.harnesses) if crow_cfg.harnesses else [crow_cfg.harness]
            for kind in pool:
                overrides[kind] = crow_cfg.binary  # crow wins over collaborator
    except AttributeError:
        pass
    return overrides


__all__ = [
    "ProbeResult",
    "binary_overrides_from_config",
    "probe_all",
    "probe_harness_version",
    "probeable_kinds",
]
