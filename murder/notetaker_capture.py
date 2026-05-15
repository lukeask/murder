"""One-shot planning capture: LLM returns fenced JSON → `notes_entries` + daily note merge."""

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


def _fallback_clean_short(raw: str) -> tuple[str, str]:
    r = raw.strip()
    cap = _SHORT_VERS_MAX_CHARS
    ell = max(0, cap - 3)
    tail = r if len(r) <= cap else f"{r[:ell]}..."
    return r, tail


def normalized_capture_fields(blob: dict[str, Any]) -> tuple[str, str]:
    cleaned = blob.get("cleaned")
    short = blob.get("short_vers")
    if isinstance(cleaned, str) and isinstance(short, str):
        cc, ss = cleaned.strip(), short.strip()
        if cc and ss:
            return cc, ss
    raise ValueError("missing cleaned/short_vers")


async def llm_normalized_capture(
    *,
    raw: str,
    system: str,
    client: APIClient | None,
    config: NotetakerConfig,
) -> tuple[str, str]:
    if client is None:
        return _fallback_clean_short(raw)
    user_body = (
        "The user submitted this planning capture (verbatim).\n"
        "Respond with ONLY the JSON object described in your system instructions.\n\n"
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
        return _fallback_clean_short(raw)
    try:
        return normalized_capture_fields(blob)
    except ValueError:
        return _fallback_clean_short(raw)


async def submit_capture(
    *,
    repo_root: Path,
    conn: sqlite3.Connection,
    raw: str,
    client: APIClient | None,
    config: NotetakerConfig,
    note_name: str | None = None,
) -> dict[str, Any]:
    """Normalize `raw` via the notetaker prompt, persist row, merge cleaned into dated note."""
    body = raw.strip()
    if not body:
        raise ValueError("empty capture")

    system = load("notetaker")
    cleaned, short_vers = await llm_normalized_capture(
        raw=body, system=system, client=client, config=config
    )
    entry_id = dbmod.insert_notes_entry(conn, raw=body, cleaned=cleaned, short_vers=short_vers)

    day = note_name if note_name else notes_mod.today_name()
    notes_mod.ensure_note(conn, repo_root, day)
    note_body = notes_mod.read_note(conn, day).rstrip()
    addition = cleaned.strip()
    merged = f"{note_body}\n\n{addition}\n".lstrip() if addition else note_body + "\n"
    if merged.strip() != note_body.strip():
        notes_mod.write_note(conn, repo_root, day, merged.rstrip() + "\n", source="agent")

    return {
        "entry_id": entry_id,
        "cleaned": cleaned,
        "short_vers": short_vers,
        "reply": short_vers,
    }
