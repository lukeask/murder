"""Lexical search candidate provider via ``rg`` (Part 9)."""

from __future__ import annotations

import asyncio
import re
import shutil
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from murder.context_compiler.candidates.models import (
    CANDIDATE_KIND_EXACT_RANGE,
    CANDIDATE_KIND_FILE,
    CANDIDATE_KIND_SEMANTIC_UNIT,
    SCORE_DIRECT_LEXICAL,
    SCORE_WEAK_TEXTUAL,
    Candidate,
    SnapshotRef,
)
from murder.context_compiler.indexing.files import FileClass, classify_path
from murder.context_compiler.indexing.queries import (
    find_unit_containing_line,
    get_file_version_by_path,
)
from murder.context_compiler.models import ContextRequest, EvidenceLedgerEntry
from murder.context_compiler.persistence.files import normalize_relative_path

_DEFAULT_RG_EXCLUDES = (
    "!**/.git/**",
    "!**/node_modules/**",
    "!**/.murder/**",
    "!**/__pycache__/**",
    "!**/.venv/**",
    "!**/venv/**",
    "!**/dist/**",
    "!**/build/**",
    "!**/target/**",
    "!**/.next/**",
    "!**/vendor/**",
)

PROVIDER_ID = "lexical"

_IDENTIFIER_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b")
_QUOTED_RE = re.compile(r"""(['"`])([^'"`]{2,120}?)\1""")
_PATH_FRAGMENT_RE = re.compile(
    r"\b[\w./\\-]+\.(?:py|ts|tsx|js|jsx|go|rs|java|kt|c|cc|cpp|h|hpp|css|html|vue|svelte)\b",
    re.IGNORECASE,
)
_ERROR_LIKE_RE = re.compile(
    r"\b(?:Error|Exception|TypeError|ValueError|KeyError|ENOENT|EACCES|"
    r"NotFound|Forbidden|Unauthorized)[:\s]*[A-Za-z0-9_./:-]{0,80}",
)
_CONFIG_KEY_RE = re.compile(r"\b[A-Z][A-Z0-9_]{2,}\b|\b[\w-]+\.[\w.-]{2,}\b")
_SELECTOR_RE = re.compile(r"(?:^|[\s(,])([.#][A-Za-z_][\w-]*)")

# Stopwords that are too common for useful lexical hits.
_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "from",
        "this",
        "that",
        "into",
        "when",
        "where",
        "which",
        "should",
        "would",
        "could",
        "must",
        "have",
        "been",
        "were",
        "are",
        "was",
        "will",
        "not",
        "but",
        "all",
        "any",
        "can",
        "may",
        "use",
        "using",
        "used",
        "via",
        "also",
        "than",
        "then",
        "them",
        "they",
        "their",
        "there",
        "here",
        "about",
        "after",
        "before",
        "between",
        "over",
        "under",
        "onto",
        "file",
        "files",
        "code",
        "function",
        "class",
        "method",
        "return",
        "true",
        "false",
        "null",
        "none",
        "implement",
        "implementation",
        "fix",
        "update",
        "create",
        "make",
        "add",
        "please",
        "need",
        "needs",
        "want",
        "like",
    }
)


@dataclass(frozen=True, slots=True)
class LexicalSearchProvider:
    """Repository-local lexical candidates over current source.

    Prefers ``rg`` subprocess; falls back to a bounded line scan when ``rg``
    is unavailable. No vector DB.
    """

    conn: sqlite3.Connection
    worktree_root: Path | None = None
    max_terms: int = 24
    max_hits_per_term: int = 40
    max_candidates: int = 120
    max_file_hits: int = 8
    rg_timeout_seconds: float = 8.0
    extra_globs_exclude: tuple[str, ...] = _DEFAULT_RG_EXCLUDES

    async def generate(
        self,
        request: ContextRequest,
        snapshot: SnapshotRef,
        prior_evidence: Sequence[EvidenceLedgerEntry],
    ) -> Sequence[Candidate]:
        del prior_evidence
        root = Path(self.worktree_root or snapshot.worktree_root)
        terms = self._extract_terms(request)
        if not terms:
            return ()

        hits = await self._search(root, terms)
        return tuple(self._hits_to_candidates(snapshot, hits)[: self.max_candidates])

    def _extract_terms(self, request: ContextRequest) -> list[str]:
        texts = [request.objective or ""]
        if request.first_message:
            texts.append(request.first_message)
        blob = "\n".join(texts)

        ordered: list[str] = []

        def add(term: str, *, weak: bool = False) -> None:
            cleaned = term.strip()
            if len(cleaned) < 3:
                return
            if cleaned.lower() in _STOPWORDS:
                return
            if cleaned not in ordered:
                ordered.append(cleaned)
            del weak

        # Explicit search_terms (Step 5 gaps) take precedence over mined terms.
        for term in request.search_terms:
            add(term)
        for m in _QUOTED_RE.finditer(blob):
            add(m.group(2))
        for m in _PATH_FRAGMENT_RE.finditer(blob):
            add(m.group(0))
        for m in _ERROR_LIKE_RE.finditer(blob):
            add(m.group(0).strip())
        for m in _SELECTOR_RE.finditer(blob):
            add(m.group(1))
        for hint in (*request.path_hints, *request.symbol_hints):
            add(hint)
        for m in _IDENTIFIER_RE.finditer(blob):
            tok = m.group(0)
            if tok.lower() in _STOPWORDS:
                continue
            # Prefer CamelCase / snake_case over pure lowercase commons.
            if tok[0].isupper() or "_" in tok or any(c.isupper() for c in tok[1:]):
                add(tok)
            elif len(tok) >= 5:
                add(tok)
        for m in _CONFIG_KEY_RE.finditer(blob):
            add(m.group(0))

        return ordered[: self.max_terms]

    async def _search(self, root: Path, terms: Sequence[str]) -> list[tuple[str, int, str, str]]:
        """Return ``(path, line, term, match_kind)`` tuples."""
        if not (root / ".git").exists():
            return self._search_scan(root, terms)
        if shutil.which("rg"):
            return await self._search_rg(root, terms)
        return self._search_scan(root, terms)

    async def _search_rg(self, root: Path, terms: Sequence[str]) -> list[tuple[str, int, str, str]]:
        hits: list[tuple[str, int, str, str]] = []
        per_file: dict[str, int] = {}
        for term in terms:
            if len(hits) >= self.max_candidates * 2:
                break
            cmd = [
                "rg",
                "--no-heading",
                "--line-number",
                "--with-filename",
                "--fixed-strings",
                "--max-count",
                str(self.max_hits_per_term),
                "--glob",
                "!**/.git/**",
            ]
            for g in self.extra_globs_exclude:
                cmd.extend(["--glob", g])
            cmd.extend(["--", term, str(root)])
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=self.rg_timeout_seconds
                )
            except TimeoutError:
                proc.kill()
                await proc.wait()
                # Partial term results make repeated retrieval nondeterministic.
                # Restart with the hermetic scanner instead.
                return self._search_scan(root, terms)
            except OSError:
                return self._search_scan(root, terms)
            if proc.returncode not in (0, 1):
                continue
            for line in stdout.decode("utf-8", errors="replace").splitlines():
                parsed = self._parse_rg_line(line, root)
                if parsed is None:
                    continue
                path, lineno = parsed
                count = per_file.get(path, 0)
                if count >= self.max_file_hits:
                    continue
                per_file[path] = count + 1
                kind = self._term_kind(term)
                hits.append((path, lineno, term, kind))
                if len(hits) >= self.max_candidates * 2:
                    break
        return hits

    def _search_scan(self, root: Path, terms: Sequence[str]) -> list[tuple[str, int, str, str]]:
        hits: list[tuple[str, int, str, str]] = []
        per_file: dict[str, int] = {}
        if not root.is_dir():
            return hits
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            try:
                rel = normalize_relative_path(str(path.relative_to(root)))
            except ValueError:
                continue
            classified = classify_path(rel, byte_count=0)
            if classified.classification == FileClass.IGNORED:
                continue
            try:
                if path.stat().st_size > 1_048_576:
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for lineno, line in enumerate(text.splitlines(), start=1):
                for term in terms:
                    if term not in line:
                        continue
                    count = per_file.get(rel, 0)
                    if count >= self.max_file_hits:
                        break
                    per_file[rel] = count + 1
                    hits.append((rel, lineno, term, self._term_kind(term)))
                    if len(hits) >= self.max_candidates * 2:
                        return hits
        return hits

    @staticmethod
    def _parse_rg_line(line: str, root: Path) -> tuple[str, int] | None:
        # path:line:content — path may contain colons on Windows; use rsplit limit.
        # Prefer splitting from the left after making path relative.
        if ":" not in line:
            return None
        # rg --with-filename yields absolute or relative path.
        # Find the line-number field: last path segment before digits.
        parts = line.split(":", 2)
        if len(parts) < 2:
            return None
        raw_path, lineno_s = parts[0], parts[1]
        if not lineno_s.isdigit():
            # Windows drive letter: C:\...:line:text → need 3+ splits.
            if len(parts) >= 3 and parts[1].isdigit() is False:
                # Try path with drive: "C", "\\foo\\bar", "12", "text"
                alt = line.split(":", 3)
                if len(alt) >= 3 and alt[2].isdigit():
                    raw_path = alt[0] + ":" + alt[1]
                    lineno_s = alt[2]
                else:
                    return None
            else:
                return None
        try:
            lineno = int(lineno_s)
        except ValueError:
            return None
        p = Path(raw_path)
        try:
            if p.is_absolute():
                rel = str(p.relative_to(root)).replace("\\", "/")
            else:
                rel = str(p).replace("\\", "/")
            rel = normalize_relative_path(rel)
        except ValueError:
            return None
        classified = classify_path(rel, byte_count=0)
        if classified.classification == FileClass.IGNORED:
            return None
        return rel, lineno

    @staticmethod
    def _term_kind(term: str) -> str:
        if _QUOTED_RE.fullmatch(f'"{term}"') or " " in term:
            return "quoted_fragment"
        if _PATH_FRAGMENT_RE.fullmatch(term):
            return "path_fragment"
        if term.startswith(".") or term.startswith("#"):
            return "selector"
        if term.isupper() and "_" in term:
            return "config_key"
        if any(x in term for x in ("Error", "Exception", "ENOENT", "EACCES")):
            return "error_string"
        return "identifier"

    def _hits_to_candidates(
        self,
        snapshot: SnapshotRef,
        hits: Sequence[tuple[str, int, str, str]],
    ) -> list[Candidate]:
        out: list[Candidate] = []
        seen: set[tuple[object, ...]] = set()
        for path, lineno, term, kind in hits:
            entry = get_file_version_by_path(
                self.conn, snapshot_id=snapshot.snapshot_id, relative_path=path
            )
            # Prefer indexed paths; still emit range candidates for dirty-only files.
            unit = find_unit_containing_line(
                self.conn,
                snapshot_id=snapshot.snapshot_id,
                relative_path=path,
                line=lineno,
            )
            score = (
                SCORE_DIRECT_LEXICAL
                if kind in {"identifier", "quoted_fragment", "error_string", "selector"}
                else SCORE_WEAK_TEXTUAL
                if kind in {"path_fragment", "config_key"}
                else SCORE_DIRECT_LEXICAL
            )
            if unit is not None:
                key: tuple[object, ...] = ("unit", unit.unit_version_id, term)
                if key in seen:
                    continue
                seen.add(key)
                out.append(
                    Candidate(
                        path=path,
                        unit_id=unit.unit_id,
                        unit_version_id=unit.unit_version_id,
                        start_line=unit.start_line,
                        end_line=unit.end_line,
                        candidate_kind=CANDIDATE_KIND_SEMANTIC_UNIT,
                        reasons=(f"lexical_{kind}",),
                        provider=PROVIDER_ID,
                        raw_score=score,
                        metadata={
                            "term": term,
                            "match_line": lineno,
                            "term_kind": kind,
                            "qualified_name": unit.qualified_name,
                            "indexed": entry is not None,
                        },
                    )
                )
            else:
                key = ("range", path, lineno, term)
                if key in seen:
                    continue
                seen.add(key)
                out.append(
                    Candidate(
                        path=path,
                        unit_id=None,
                        unit_version_id=None,
                        start_line=lineno,
                        end_line=lineno,
                        candidate_kind=(
                            CANDIDATE_KIND_EXACT_RANGE if entry is not None else CANDIDATE_KIND_FILE
                        ),
                        reasons=(f"lexical_{kind}",),
                        provider=PROVIDER_ID,
                        raw_score=score,
                        metadata={
                            "term": term,
                            "match_line": lineno,
                            "term_kind": kind,
                            "indexed": entry is not None,
                        },
                    )
                )
            if len(out) >= self.max_candidates:
                break
        return out


__all__ = ["LexicalSearchProvider", "PROVIDER_ID"]
