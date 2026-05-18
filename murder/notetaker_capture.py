"""Notetaker capture: durable note create first, async LLM metadata second."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from murder import db as dbmod
from murder import notes as notes_mod
from murder.clients.base import APIClient
from murder.config import NotetakerConfig
from murder.prompts import load

_SHORT_VERS_MAX_CHARS = 240

_JSON_FENCE = re.compile(r"```(?:json)?\s*\n([\s\S]*?)```", re.IGNORECASE)


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
    entry_id = dbmod.insert_notes_entry(conn, raw=body, cleaned=body, short_vers=initial_short)
    note_name = notes_mod.create_timestamped_note(conn, repo_root, body, source="agent")
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
    while notes_mod.active_note_name_exists(conn, repo_root, name, exclude=exclude):
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
    system = load("notetaker")
    meta = await llm_capture_metadata(
        raw=body,
        system=system,
        client=client,
        config=config,
    )
    short_vers = meta["short_vers"]
    dbmod.update_notes_entry_short_vers(conn, entry_id, short_vers)

    resolved_name = note_name
    title = meta["one_or_two_word_title"]
    slug = _slugify_title(title)
    if slug:
        if notes_mod.active_note_name_exists(conn, repo_root, slug, exclude=note_name):
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
                dbmod.update_notes_entry_short_vers(conn, entry_id, short_vers)
                slug = retry_slug
        target = _collision_safe_name(conn, repo_root, slug, exclude=note_name)
        if target != note_name:
            resolved_name = notes_mod.rename_note(conn, repo_root, note_name, target)

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
            entry_id = dbmod.insert_notes_entry(
                conn, raw=body, cleaned=body, short_vers=initial_short
            )
        if dbmod.get_note(conn, note_name) is None:
            notes_mod.write_note(conn, repo_root, note_name, body.rstrip() + "\n", source="agent")
        else:
            note_body = notes_mod.read_note(conn, note_name).rstrip()
            merged = f"{note_body}\n\n{body}\n".lstrip()
            if merged.strip() != note_body.strip():
                notes_mod.write_note(
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
