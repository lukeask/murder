"""Active dirty-worktree diff candidate provider (Part 9).

Inspects the live worktree; does not persist diff bodies in the index.
"""

from __future__ import annotations

import asyncio
import re
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from murder.context_compiler.candidates.models import (
    CANDIDATE_KIND_DIFF_PATH,
    CANDIDATE_KIND_SEMANTIC_UNIT,
    SCORE_ACTIVE_DIFF_OVERLAP,
    Candidate,
    SnapshotRef,
)
from murder.context_compiler.indexing.queries import (
    find_unit_containing_line,
    get_file_version_by_path,
    list_resource_links_for_path,
    list_semantic_units_by_path,
)
from murder.context_compiler.models import ContextRequest, EvidenceLedgerEntry
from murder.context_compiler.persistence.files import get_file, normalize_relative_path
from murder.context_compiler.persistence.records import SemanticUnitVersionRecord

PROVIDER_ID = "active_diff"

# Unified diff hunk header: @@ -old_start,old_count +new_start,new_count @@
_HUNK_RE = re.compile(
    r"^@@\s+-\d+(?:,\d+)?\s+\+(\d+)(?:,(\d+))?\s+@@",
)


@dataclass(frozen=True, slots=True)
class ActiveDiffProvider:
    """Dirty worktree changes → path and overlapping unit candidates."""

    conn: sqlite3.Connection
    worktree_root: Path | None = None
    max_candidates: int = 150
    git_timeout_seconds: float = 10.0

    async def generate(
        self,
        request: ContextRequest,
        snapshot: SnapshotRef,
        prior_evidence: Sequence[EvidenceLedgerEntry],
    ) -> Sequence[Candidate]:
        del request, prior_evidence
        root = Path(self.worktree_root or snapshot.worktree_root)
        changed = await self._changed_paths(root)
        hunks = await self._hunk_ranges(root, changed)

        out: list[Candidate] = []
        seen: set[tuple[object, ...]] = set()

        for path in changed:
            key = ("file", path)
            if key not in seen:
                seen.add(key)
                out.append(
                    Candidate(
                        path=path,
                        unit_id=None,
                        unit_version_id=None,
                        start_line=None,
                        end_line=None,
                        candidate_kind=CANDIDATE_KIND_DIFF_PATH,
                        reasons=("active_diff_changed_path",),
                        provider=PROVIDER_ID,
                        raw_score=SCORE_ACTIVE_DIFF_OVERLAP,
                        metadata={"change": "path"},
                    )
                )

            ranges = hunks.get(path, [])
            for start, end in ranges:
                for unit in self._units_overlapping(snapshot, path, start=start, end=end):
                    ukey = ("unit", unit.unit_version_id)
                    if ukey in seen:
                        continue
                    seen.add(ukey)
                    out.append(
                        Candidate(
                            path=path,
                            unit_id=unit.unit_id,
                            unit_version_id=unit.unit_version_id,
                            start_line=unit.start_line,
                            end_line=unit.end_line,
                            candidate_kind=CANDIDATE_KIND_SEMANTIC_UNIT,
                            reasons=("active_diff_unit_overlap",),
                            provider=PROVIDER_ID,
                            raw_score=SCORE_ACTIVE_DIFF_OVERLAP,
                            metadata={
                                "hunk_start": start,
                                "hunk_end": end,
                                "qualified_name": unit.qualified_name,
                            },
                        )
                    )

                owner = find_unit_containing_line(
                    self.conn,
                    snapshot_id=snapshot.snapshot_id,
                    relative_path=path,
                    line=start,
                )
                if owner is not None:
                    ukey = ("owner", owner.unit_version_id)
                    if ukey not in seen:
                        seen.add(ukey)
                        out.append(
                            Candidate(
                                path=path,
                                unit_id=owner.unit_id,
                                unit_version_id=owner.unit_version_id,
                                start_line=owner.start_line,
                                end_line=owner.end_line,
                                candidate_kind=CANDIDATE_KIND_SEMANTIC_UNIT,
                                reasons=("active_diff_owning_unit",),
                                provider=PROVIDER_ID,
                                raw_score=SCORE_ACTIVE_DIFF_OVERLAP,
                                metadata={
                                    "hunk_start": start,
                                    "qualified_name": owner.qualified_name,
                                },
                            )
                        )

            # Resource-linked components for changed templates/styles.
            out.extend(self._resource_linked_components(snapshot, path, seen=seen))
            if len(out) >= self.max_candidates:
                return tuple(out[: self.max_candidates])

        return tuple(out[: self.max_candidates])

    def _units_overlapping(
        self,
        snapshot: SnapshotRef,
        path: str,
        *,
        start: int,
        end: int,
    ) -> list[SemanticUnitVersionRecord]:
        units = list_semantic_units_by_path(
            self.conn, snapshot_id=snapshot.snapshot_id, relative_path=path
        )
        return [u for u in units if not (u.end_line < start or u.start_line > end)]

    def _resource_linked_components(
        self,
        snapshot: SnapshotRef,
        path: str,
        *,
        seen: set[tuple[object, ...]],
    ) -> list[Candidate]:
        """When a template/style changed, surface linked component units/files."""
        lower = path.lower()
        is_resource = lower.endswith(
            (".html", ".htm", ".vue", ".svelte", ".css", ".scss", ".sass", ".less")
        )
        if not is_resource:
            return []

        results: list[Candidate] = []
        # Incoming resource relationships: components that link TO this file.
        entry = get_file_version_by_path(
            self.conn, snapshot_id=snapshot.snapshot_id, relative_path=path
        )
        if entry is None:
            return results

        rows = self.conn.execute(
            """
            SELECT r.source_unit_version_id, r.source_file_version_id,
                   r.relation_kind, f.path AS source_path,
                   suv.unit_id, suv.unit_version_id, suv.start_line, suv.end_line,
                   suv.qualified_name
              FROM relationships r
              JOIN snapshot_files sf ON sf.file_version_id = r.source_file_version_id
              JOIN files f ON f.file_id = sf.file_id
              LEFT JOIN semantic_unit_versions suv
                ON suv.unit_version_id = r.source_unit_version_id
             WHERE sf.snapshot_id = ?
               AND r.target_file_id = ?
               AND (
                    r.relation_kind LIKE 'resource:%'
                    OR r.relation_kind IN ('template_of', 'style_of')
               )
             ORDER BY r.relationship_id
            """,
            (snapshot.snapshot_id, entry.file.file_id),
        ).fetchall()

        for row in rows:
            if row["unit_version_id"] is not None:
                ukey = ("resource_unit", int(row["unit_version_id"]))
                if ukey in seen:
                    continue
                seen.add(ukey)
                results.append(
                    Candidate(
                        path=str(row["source_path"]),
                        unit_id=int(row["unit_id"]),
                        unit_version_id=int(row["unit_version_id"]),
                        start_line=int(row["start_line"]),
                        end_line=int(row["end_line"]),
                        candidate_kind=CANDIDATE_KIND_SEMANTIC_UNIT,
                        reasons=("active_diff_resource_linked_component",),
                        provider=PROVIDER_ID,
                        raw_score=SCORE_ACTIVE_DIFF_OVERLAP,
                        metadata={
                            "changed_resource": path,
                            "relation_kind": str(row["relation_kind"]),
                            "qualified_name": str(row["qualified_name"]),
                        },
                    )
                )
            else:
                fkey = ("resource_file", str(row["source_path"]))
                if fkey in seen:
                    continue
                seen.add(fkey)
                results.append(
                    Candidate(
                        path=str(row["source_path"]),
                        unit_id=None,
                        unit_version_id=None,
                        start_line=None,
                        end_line=None,
                        candidate_kind=CANDIDATE_KIND_DIFF_PATH,
                        reasons=("active_diff_resource_linked_component",),
                        provider=PROVIDER_ID,
                        raw_score=SCORE_ACTIVE_DIFF_OVERLAP,
                        metadata={
                            "changed_resource": path,
                            "relation_kind": str(row["relation_kind"]),
                        },
                    )
                )

        # Also check resource_links table on other files pointing here — already
        # covered via companion relationships when resolver ran. Surface links
        # declared ON the changed file toward components when present.
        for link in list_resource_links_for_path(
            self.conn, snapshot_id=snapshot.snapshot_id, relative_path=path
        ):
            if link.target_file_id is None:
                continue
            target = get_file(self.conn, link.target_file_id)
            if target is None:
                continue
            fkey = ("resource_target", target.path)
            if fkey in seen:
                continue
            seen.add(fkey)
            results.append(
                Candidate(
                    path=target.path,
                    unit_id=None,
                    unit_version_id=None,
                    start_line=None,
                    end_line=None,
                    candidate_kind=CANDIDATE_KIND_DIFF_PATH,
                    reasons=("active_diff_resource_link_target",),
                    provider=PROVIDER_ID,
                    raw_score=SCORE_ACTIVE_DIFF_OVERLAP,
                    metadata={
                        "changed_resource": path,
                        "resource_kind": link.resource_kind,
                    },
                )
            )
        return results

    async def _git(self, root: Path, *args: str) -> tuple[int, str]:
        try:
            proc = await asyncio.create_subprocess_exec(
                "git",
                "-C",
                str(root),
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.git_timeout_seconds
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return 1, ""
        except OSError:
            return 1, ""
        return int(proc.returncode or 0), stdout.decode("utf-8", errors="replace")

    async def _changed_paths(self, root: Path) -> list[str]:
        """Staged + unstaged + untracked paths relative to HEAD (dirty worktree)."""
        # Do not spawn git for ordinary fixture/source directories. Besides
        # wasting two timeout windows, walking up to an unrelated parent repo
        # would report paths outside the indexed worktree.
        if not (root / ".git").exists():
            return []
        paths: list[str] = []

        # Combined staged+unstaged vs HEAD.
        rc, out = await self._git(root, "diff", "--name-only", "HEAD")
        if rc == 0:
            paths.extend(line for line in out.splitlines() if line)

        # Untracked (non-ignored).
        rc, out = await self._git(root, "ls-files", "--others", "--exclude-standard")
        if rc == 0:
            paths.extend(line for line in out.splitlines() if line)

        # If not a git repo / no HEAD, fall back to empty.
        normalized: list[str] = []
        seen: set[str] = set()
        for raw in paths:
            try:
                path = normalize_relative_path(raw.replace("\\", "/"))
            except ValueError:
                continue
            if path in seen:
                continue
            seen.add(path)
            normalized.append(path)
        normalized.sort()
        return normalized

    async def _hunk_ranges(
        self, root: Path, paths: Sequence[str]
    ) -> dict[str, list[tuple[int, int]]]:
        if not paths:
            return {}
        rc, out = await self._git(root, "diff", "--unified=0", "HEAD", "--", *paths)
        if rc != 0 or not out:
            # Untracked files: treat whole file as changed if it exists.
            result: dict[str, list[tuple[int, int]]] = {}
            for path in paths:
                abs_path = root / path
                if not abs_path.is_file():
                    continue
                try:
                    text = abs_path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                lines = text.count("\n") + (0 if text.endswith("\n") or not text else 1)
                lines = max(lines, 1)
                result[path] = [(1, lines)]
            return result

        result = self._parse_unified_diff(out)
        for path in paths:
            if path in result:
                continue
            abs_path = root / path
            if abs_path.is_file():
                try:
                    text = abs_path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                lines = max(1, text.count("\n") + (0 if text.endswith("\n") or not text else 1))
                result[path] = [(1, lines)]
        return result

    @staticmethod
    def _parse_unified_diff(diff_text: str) -> dict[str, list[tuple[int, int]]]:
        current: str | None = None
        result: dict[str, list[tuple[int, int]]] = {}
        for line in diff_text.splitlines():
            if line.startswith("+++ "):
                raw = line[4:].strip()
                if raw == "/dev/null":
                    current = None
                    continue
                if raw.startswith("b/"):
                    raw = raw[2:]
                try:
                    current = normalize_relative_path(raw.replace("\\", "/"))
                except ValueError:
                    current = None
                continue
            if current is None:
                continue
            m = _HUNK_RE.match(line)
            if not m:
                continue
            start = int(m.group(1))
            count = int(m.group(2) or "1")
            if count == 0:
                # Deletion-only hunk: still attribute to the nearby line.
                end = start if start > 0 else 1
                start = max(1, start)
            else:
                end = start + count - 1
            result.setdefault(current, []).append((start, end))
        return result


__all__ = ["ActiveDiffProvider", "PROVIDER_ID"]
