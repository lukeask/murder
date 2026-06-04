#!/usr/bin/env python3
"""Dev gate: probe installed harness CLI versions and add verified ones to the manifest.

Usage:
    python tools/testing/check_harness_versions.py [--dry-run]

For each harness whose installed version is not yet in verified_versions.yaml:
  1. Run the harness unit test suite.
  2. Pass → append the version to the manifest (inheriting the latest adapter_id).
  3. Fail → print a loud alert; you write a new adapter and re-run.

The manifest file is rewritten in-place on success.  Review with ``git diff``
before committing — this script never commits.

See .murder/notes/plan-harness-versioning-phase2.md for the full Phase 2 plan.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO_ROOT / "murder" / "llm" / "harnesses" / "verified_versions.yaml"
TEST_PATHS = ["tests/unit/test_harness_adapters.py",
              "tests/unit/test_harness_session.py",
              "tests/unit/test_harness_transcripts.py",
              "tests/unit/test_harness_model_selection.py",
              "tests/unit/test_harness_interrupt.py",
              "tests/unit/test_harness_usage_parsing.py"]

sys.path.insert(0, str(REPO_ROOT))

from murder.llm.harnesses.version_probe import probe_all, probeable_kinds  # noqa: E402
from murder.llm.harnesses.versioning import load_manifest, normalize_version  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ANSI_GREEN = "\033[32m"
_ANSI_RED = "\033[31m"
_ANSI_YELLOW = "\033[33m"
_ANSI_BOLD = "\033[1m"
_ANSI_RESET = "\033[0m"


def _ok(msg: str) -> None:
    print(f"{_ANSI_GREEN}✓{_ANSI_RESET} {msg}")


def _fail(msg: str) -> None:
    print(f"{_ANSI_RED}✗{_ANSI_RESET} {msg}", file=sys.stderr)


def _warn(msg: str) -> None:
    print(f"{_ANSI_YELLOW}⚠{_ANSI_RESET} {msg}")


def _header(msg: str) -> None:
    print(f"\n{_ANSI_BOLD}{msg}{_ANSI_RESET}")


def _run_pytest(test_paths: list[str], repo_root: Path) -> bool:
    """Run pytest against *test_paths*; stream output; return True on pass."""
    cmd = [sys.executable, "-m", "pytest", "--tb=short", "-q", *test_paths]
    result = subprocess.run(cmd, cwd=str(repo_root))
    return result.returncode == 0


def _latest_adapter_id(kind_entries: dict) -> str:
    """Return the adapter_id from the highest version key in kind_entries."""
    if not kind_entries:
        return f"unknown_default"

    def _vtup(v: str) -> tuple[int, ...]:
        try:
            return tuple(int(x) for x in v.split("."))
        except ValueError:
            return (0,)

    latest_key = max(kind_entries, key=_vtup)
    return kind_entries[latest_key]["adapter"]


def _write_manifest(manifest: dict, path: Path) -> None:
    """Write manifest back to *path*, preserving the header comment block."""
    _HEADER = """\
# Verified harness CLI versions → adapter IDs
#
# This file is written by tools/testing/check_harness_versions.py (dev gate).
# The service worker reads it at startup; it never writes here.
#
# Resolution rules (implemented in versioning.py):
#   - Exact match → use that adapter_id
#   - Installed > max key → use latest adapter_id, log version_newer_than_supported
#   - Kind absent → use "<kind>_default" as fallback
#
# See .murder/notes/plan-harness-versioning-phase2.md for Phase 2 plans.
"""
    buf = io.StringIO()
    buf.write(_HEADER)
    buf.write("\n")
    for kind, entries in sorted(manifest.items()):
        buf.write(f"{kind}:\n")
        if not entries:
            buf.write(
                f"  # No verified version yet — forward-compat rule applies.\n"
            )
        else:
            for version, meta in sorted(
                entries.items(),
                key=lambda kv: tuple(
                    int(x) for x in kv[0].split(".") if x.isdigit()
                ),
            ):
                buf.write(f'  "{version}":\n')
                buf.write(f'    adapter: {meta["adapter"]}\n')
                buf.write(f'    verified_at: "{meta["verified_at"]}"\n')
        buf.write("\n")
    path.write_text(buf.getvalue())


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def _probe_versions() -> dict[str, str | None]:
    """Return kind → normalized_version (or None if unavailable)."""
    results = await probe_all(probeable_kinds())
    return {
        r.kind: normalize_version(r.raw) if r.raw else None
        for r in results
    }


def main(dry_run: bool = False) -> int:
    from datetime import date

    today = date.today().isoformat()

    _header("Probing installed harness CLI versions…")
    versions = asyncio.run(_probe_versions())
    for kind, ver in versions.items():
        if ver:
            print(f"  {kind}: {ver}")
        else:
            _warn(f"  {kind}: version unavailable (skipping)")

    manifest = load_manifest(MANIFEST_PATH)
    # Ensure every probeable kind has a dict entry (may be None from YAML)
    for kind in probeable_kinds():
        if manifest.get(kind) is None:
            manifest[kind] = {}

    _header("Comparing to manifest…")
    new_versions: dict[str, str] = {}  # kind → normalized version
    for kind, ver in versions.items():
        if ver is None:
            continue
        existing = manifest.get(kind) or {}
        if ver in existing:
            _ok(f"{kind} {ver} — already verified")
        else:
            _warn(f"{kind} {ver} — NEW (not in manifest)")
            new_versions[kind] = ver

    if not new_versions:
        _ok("\nAll probed versions are already in the manifest. Nothing to do.")
        return 0

    _header(f"Running harness tests for {len(new_versions)} new version(s)…")
    existing_paths = [p for p in TEST_PATHS if (REPO_ROOT / p).exists()]
    if not existing_paths:
        _fail("No test files found — check TEST_PATHS in this script.")
        return 2

    passed = _run_pytest(existing_paths, REPO_ROOT)

    if not passed:
        print()
        _fail("=" * 60)
        _fail("TESTS FAILED — new versions NOT added to manifest.")
        _fail("")
        _fail("Next steps:")
        for kind, ver in new_versions.items():
            _fail(f"  • Investigate {kind} {ver}: check what changed in the adapter")
            _fail(f"    and add a version-specific adapter if needed.")
            _fail(f"    See .murder/notes/plan-harness-versioning-phase2.md")
        _fail("=" * 60)
        return 1

    _header("Tests passed — updating manifest…")
    for kind, ver in new_versions.items():
        existing = manifest.get(kind) or {}
        adapter_id = _latest_adapter_id(existing)
        manifest[kind][ver] = {"adapter": adapter_id, "verified_at": today}
        _ok(f"  {kind} {ver} → adapter={adapter_id}")

    if dry_run:
        _warn("\n--dry-run: manifest NOT written. Would have written:")
        print()
        for kind, ver in new_versions.items():
            print(f'  {kind}:')
            print(f'    "{ver}":')
            print(f'      adapter: {manifest[kind][ver]["adapter"]}')
            print(f'      verified_at: "{today}"')
    else:
        _write_manifest(manifest, MANIFEST_PATH)
        _ok(f"\nManifest updated: {MANIFEST_PATH.relative_to(REPO_ROOT)}")
        print("  Review with: git diff murder/llm/harnesses/verified_versions.yaml")

    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="probe and test but do not write the manifest")
    args = parser.parse_args()
    sys.exit(main(dry_run=args.dry_run))
