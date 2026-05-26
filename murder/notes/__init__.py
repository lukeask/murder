"""DB-backed planning notes — the "notetaker" scratchpad docs.

Notes are dated markdown documents (`.murder/notes/<YYYY-MM-DD>.md`).
Runtime maintains a DB+file mirror and records note revisions in
`note_revisions` for safety/auditability.

Also contains capture logic: durable note create first, async LLM
metadata resolution second.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from murder.clients.base import APIClient
from murder.config import NotetakerConfig
from murder.persistence import notes as notes_db
from murder.persistence import notetaker as notetaker_db
from murder.prompts import load as _load_prompt
from murder.storage.filesystem import atomic_write_text
from murder.storage.paths import note_md, notes_dir

# ---------------------------------------------------------------------------
# Capture constants
# ---------------------------------------------------------------------------

_SHORT_VERS_MAX_CHARS = 240
_JSON_FENCE = re.compile(r"```(?:json)?\s*\n([\s\S]*?)```", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Notes CRUD helpers
# ---------------------------------------------------------------------------


def today_name() -> str:
    """The note name for today (`YYYY-MM-DD`, UTC — matches db._now)."""
    return datetime.utcnow().date().isoformat()


def timestamp_name(now: datetime | None = None) -> str:
    """Filesystem-safe provisional name for immediate capture notes."""
    dt = now or datetime.utcnow()
    return dt.strftime("%Y%m%dT%H%M%S%fZ")


def retired_notes_dir(repo_root: Path) -> Path:
    return notes_dir(repo_root) / "retired_notes"


def _rel_path(repo_root: Path, name: str) -> str:
    return str(note_md(repo_root, name).relative_to(repo_root))


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _record_revision(conn: sqlite3.Connection, name: str, body: str, *, source: str) -> None:
    notes_db.insert_note_revision(
        conn,
        name,
        source=source,
        body=body,
        content_hash=content_hash(body),
    )


def ensure_note(conn: sqlite3.Connection, repo_root: Path, name: str) -> dict[str, Any]:
    """Return note row for `name`, importing existing files without clobbering."""
    row = notes_db.get_note(conn, name)
    rel = _rel_path(repo_root, name)
    path = repo_root / rel
    if row is not None:
        if not path.exists():
            atomic_write_text(path, str(row["body"]))
        return row
    if path.exists():
        body = path.read_text(encoding="utf-8")
        notes_db.upsert_note(conn, name, body=body, materialized_path=rel)
        _record_revision(conn, name, body, source="bootstrap")
    else:
        body = ""
        notes_db.upsert_note(conn, name, body=body, materialized_path=rel)
        atomic_write_text(path, body)
        _record_revision(conn, name, body, source="bootstrap")
    return notes_db.get_note(conn, name) or {"name": name, "body": body, "materialized_path": rel}


def read_note(conn: sqlite3.Connection, name: str) -> str:
    row = notes_db.get_note(conn, name)
    return str(row["body"]) if row else ""


def write_note(
    conn: sqlite3.Connection,
    repo_root: Path,
    name: str,
    body: str,
    *,
    source: str = "agent",
) -> None:
    """Replace the body of note `name` in the DB and re-materialize its file."""
    existing = notes_db.get_note(conn, name)
    old_body = str(existing["body"]) if existing is not None else None
    rel = _rel_path(repo_root, name)
    notes_db.upsert_note(conn, name, body=body, materialized_path=rel)
    atomic_write_text(repo_root / rel, body)
    if old_body != body:
        _record_revision(conn, name, body, source=source)


def create_timestamped_note(
    conn: sqlite3.Connection,
    repo_root: Path,
    body: str,
    *,
    source: str = "agent",
    now: datetime | None = None,
) -> str:
    """Create a durable provisional note file and DB mirror immediately."""
    base = timestamp_name(now)
    name = base
    i = 2
    while notes_db.get_note(conn, name) is not None or note_md(repo_root, name).exists():
        name = f"{base}-{i}"
        i += 1
    rel = _rel_path(repo_root, name)
    text = body.rstrip() + "\n"
    atomic_write_text(repo_root / rel, text)
    notes_db.upsert_note(conn, name, body=text, materialized_path=rel)
    _record_revision(conn, name, text, source=source)
    return name


def active_note_name_exists(
    conn: sqlite3.Connection,
    repo_root: Path,
    name: str,
    *,
    exclude: str | None = None,
) -> bool:
    if name == exclude:
        return False
    row = notes_db.get_note(conn, name)
    if row is not None and str(row.get("status", "active")) == "active":
        return True
    path = note_md(repo_root, name)
    return path.exists()


def rename_note(
    conn: sqlite3.Connection,
    repo_root: Path,
    old_name: str,
    new_name: str,
) -> str:
    """Rename an active note file and DB row, preserving the DB UUID identity."""
    if old_name == new_name:
        return old_name
    if active_note_name_exists(conn, repo_root, new_name, exclude=old_name):
        raise FileExistsError(f"note already exists: {new_name}")
    row = notes_db.get_note(conn, old_name)
    if row is None:
        raise FileNotFoundError(f"note not found: {old_name}")
    old_path = repo_root / str(row["materialized_path"])
    if not old_path.exists():
        old_path = note_md(repo_root, old_name)
    new_path = note_md(repo_root, new_name)
    new_path.parent.mkdir(parents=True, exist_ok=True)
    if old_path.exists():
        old_path.rename(new_path)
    else:
        atomic_write_text(new_path, str(row["body"]))
    notes_db.rename_note(
        conn,
        old_name,
        new_name,
        materialized_path=str(new_path.relative_to(repo_root)),
    )
    return new_name


def retire_note(conn: sqlite3.Connection, repo_root: Path, name: str) -> Path:
    """Move an active note out of the sidebar into `.murder/notes/retired_notes/`."""
    row = notes_db.get_note(conn, name)
    if row is None:
        raise FileNotFoundError(f"note not found: {name}")
    old_path = repo_root / str(row["materialized_path"])
    if not old_path.exists():
        old_path = note_md(repo_root, name)
    dest_dir = retired_notes_dir(repo_root)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{name}.md"
    if dest.exists():
        base = dest_dir / name
        i = 2
        while True:
            candidate = base.with_name(f"{name}-{i}.md")
            if not candidate.exists():
                dest = candidate
                break
            i += 1
    if old_path.exists():
        old_path.rename(dest)
    else:
        atomic_write_text(dest, str(row["body"]))
    notes_db.mark_note_retired(
        conn,
        name,
        materialized_path=str(dest.relative_to(repo_root)),
    )
    return dest


def latest_prior_note(conn: sqlite3.Connection, exclude: str) -> tuple[str, str] | None:
    """The most recently-named non-empty note other than `exclude`, as (name, body)."""
    for row in notes_db.list_notes(conn):
        if row["name"] != exclude and row["size"]:
            full = notes_db.get_note(conn, row["name"])
            if full:
                return row["name"], str(full["body"])
    return None


# ---------------------------------------------------------------------------
# Capture helpers (LLM metadata extraction)
# ---------------------------------------------------------------------------


def extract_json_fence(text: str) -> dict[str, Any] | None:
    """Parse the first ```json fenced block, or fall back to a bare JSON object."""
    m = _JSON_FENCE.search(text)
    block = text.strip()
    raw_json = ""
    if m:
        raw_json = m.group(1).strip()
    elif block.startswith("{"):
        raw_json = block
    else:
        return None
    try:
        parsed = json.loads(raw_json)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _fallback_short_vers(raw: str) -> str:
    r = raw.strip()
    cap = _SHORT_VERS_MAX_CHARS
    ell = max(0, cap - 3)
    return r if len(r) <= cap else f"{r[:ell]}..."


def _slugify_title(title: str) -> str:
    words = re.findall(r"[a-z0-9]+", title.lower())
    return "-".join(words)


def short_vers_fields(blob: dict[str, Any]) -> str:
    short = blob.get("short_vers")
    if isinstance(short, str):
        ss = short.strip()
        if ss:
            return ss
    raise ValueError("missing short_vers")


def capture_metadata_fields(blob: dict[str, Any]) -> dict[str, str]:
    short = short_vers_fields(blob)
    title = blob.get("one_or_two_word_title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("missing one_or_two_word_title")
    return {
        "short_vers": short,
        "one_or_two_word_title": title.strip(),
    }


def normalized_capture_fields(blob: dict[str, Any]) -> tuple[str, str, str]:
    """Compatibility parser for older tests plus the new title field."""
    cleaned = blob.get("cleaned")
    if not isinstance(cleaned, str) or not cleaned.strip():
        raise ValueError("missing cleaned")
    meta = capture_metadata_fields(blob)
    return cleaned.strip(), meta["short_vers"], meta["one_or_two_word_title"]


async def llm_capture_metadata(
    *,
    raw: str,
    system: str,
    client: APIClient | None,
    config: NotetakerConfig,
    avoid_titles: tuple[str, ...] = (),
) -> dict[str, str]:
    if client is None:
        return {"short_vers": _fallback_short_vers(raw), "one_or_two_word_title": ""}
    avoid = ""
    if avoid_titles:
        avoid = "\nAvoid these already-used titles: " + ", ".join(avoid_titles) + "\n"
    user_body = (
        "The user submitted this planning capture (verbatim).\n"
        "Respond with ONLY the JSON object described in your system instructions.\n\n"
        f"{avoid}"
        "<<<CAPTURE>>>\n"
        f"{raw.strip()}\n"
        "<<<END>>>\n"
    )
    r = await client.complete(
        model=config.model,
        system=system,
        messages=[{"role": "user", "content": user_body}],
        tools=None,
        max_tokens=config.max_tokens,
        temperature=0.0,
    )
    text_out = (r.text or "").strip()
    blob = extract_json_fence(text_out)
    if blob is None:
        return {"short_vers": _fallback_short_vers(raw), "one_or_two_word_title": ""}
    try:
        return capture_metadata_fields(blob)
    except ValueError:
        try:
            return {
                "short_vers": short_vers_fields(blob),
                "one_or_two_word_title": "",
            }
        except ValueError:
            pass
        return {"short_vers": _fallback_short_vers(raw), "one_or_two_word_title": ""}


async def llm_short_vers(
    *,
    raw: str,
    system: str,
    client: APIClient | None,
    config: NotetakerConfig,
) -> str:
    return (
        await llm_capture_metadata(
            raw=raw,
            system=system,
            client=client,
            config=config,
        )
    )["short_vers"]


async def llm_normalized_capture(
    *,
    raw: str,
    system: str,
    client: APIClient | None,
    config: NotetakerConfig,
) -> tuple[str, str]:
    meta = await llm_capture_metadata(
        raw=raw,
        system=system,
        client=client,
        config=config,
    )
    return raw.strip(), meta["short_vers"]


def create_durable_capture(
    *,
    repo_root: Path,
    conn: sqlite3.Connection,
    raw: str,
) -> dict[str, Any]:
    """Synchronously create the note file + DB rows before any LLM call."""
    body = raw.strip()
    if not body:
        raise ValueError("empty capture")
    initial_short = _fallback_short_vers(body)
    entry_id = notetaker_db.insert_notes_entry(
        conn, raw=body, cleaned=body, short_vers=initial_short
    )
    note_name = create_timestamped_note(conn, repo_root, body, source="agent")
    return {
        "entry_id": entry_id,
        "note_name": note_name,
        "cleaned": body,
        "short_vers": initial_short,
        "reply": initial_short,
    }


def _collision_safe_name(
    conn: sqlite3.Connection,
    repo_root: Path,
    base: str,
    *,
    exclude: str,
) -> str:
    name = base
    i = 2
    while active_note_name_exists(conn, repo_root, name, exclude=exclude):
        name = f"{base}-{i}"
        i += 1
    return name


async def resolve_capture_note(
    *,
    repo_root: Path,
    conn: sqlite3.Connection,
    raw: str,
    entry_id: int,
    note_name: str,
    client: APIClient | None,
    config: NotetakerConfig,
) -> dict[str, Any]:
    """Update short_vers/title metadata and rename provisional timestamp notes."""
    body = raw.strip()
    system = _load_prompt("notetaker")
    meta = await llm_capture_metadata(
        raw=body,
        system=system,
        client=client,
        config=config,
    )
    short_vers = meta["short_vers"]
    notetaker_db.update_notes_entry_short_vers(conn, entry_id, short_vers)

    resolved_name = note_name
    title = meta["one_or_two_word_title"]
    slug = _slugify_title(title)
    if slug:
        if active_note_name_exists(conn, repo_root, slug, exclude=note_name):
            retry = await llm_capture_metadata(
                raw=body,
                system=system,
                client=client,
                config=config,
                avoid_titles=(title, slug),
            )
            retry_slug = _slugify_title(retry["one_or_two_word_title"])
            if retry_slug:
                short_vers = retry["short_vers"]
                notetaker_db.update_notes_entry_short_vers(conn, entry_id, short_vers)
                slug = retry_slug
        target = _collision_safe_name(conn, repo_root, slug, exclude=note_name)
        if target != note_name:
            resolved_name = rename_note(conn, repo_root, note_name, target)

    return {
        "entry_id": entry_id,
        "note_name": resolved_name,
        "cleaned": body,
        "short_vers": short_vers,
        "reply": short_vers,
    }


async def submit_capture(
    *,
    repo_root: Path,
    conn: sqlite3.Connection,
    raw: str,
    client: APIClient | None,
    config: NotetakerConfig,
    note_name: str | None = None,
    entry_id: int | None = None,
) -> dict[str, Any]:
    """Persist raw immediately, set short_vers/title, and maybe rename the note."""
    body = raw.strip()
    if not body:
        raise ValueError("empty capture")

    if note_name is None:
        created = create_durable_capture(repo_root=repo_root, conn=conn, raw=body)
        note_name = str(created["note_name"])
        entry_id = int(created["entry_id"])
    else:
        initial_short = _fallback_short_vers(body)
        if entry_id is None:
            entry_id = notetaker_db.insert_notes_entry(
                conn, raw=body, cleaned=body, short_vers=initial_short
            )
        if notes_db.get_note(conn, note_name) is None:
            write_note(conn, repo_root, note_name, body.rstrip() + "\n", source="agent")
        else:
            note_body = read_note(conn, note_name).rstrip()
            merged = f"{note_body}\n\n{body}\n".lstrip()
            if merged.strip() != note_body.strip():
                write_note(
                    conn,
                    repo_root,
                    note_name,
                    merged.rstrip() + "\n",
                    source="agent",
                )

    return await resolve_capture_note(
        repo_root=repo_root,
        conn=conn,
        raw=body,
        entry_id=int(entry_id),
        note_name=note_name,
        client=client,
        config=config,
    )
