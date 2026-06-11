"""Note I/O concern extracted from the Orchestrator (move-code refactor)."""

from __future__ import annotations

from typing import Any

from murder.app.service.runtime_scope import OrchestratorHost
from murder.bus import Entity
from murder.llm.clients import resolve_role_client
from murder.work import notes as notes_mod


class NoteOps:
    """Thin wrappers over ``notes_mod`` keyed on an ``OrchestratorHost``."""

    def __init__(self, rt: OrchestratorHost) -> None:
        self.rt = rt

    async def submit_notetaker_capture(self, payload: dict[str, Any]) -> dict[str, Any]:
        assert self.rt.db is not None

        raw = payload.get("raw")
        if raw is None:
            raw = payload.get("text")
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError(
                "notetaker.capture.submit requires non-empty payload.raw or payload.text"
            )

        title = payload.get("title")
        if not isinstance(title, str) or not title.strip():
            title = None
        else:
            title = title.strip()

        client = resolve_role_client(self.rt.config.notetaker)
        result = await notes_mod.submit_capture(
            repo_root=self.rt.repo_root,
            conn=self.rt.db,
            raw=raw.strip(),
            client=client,
            config=self.rt.config.notetaker,
            note_name=notes_mod.today_name(),
            title=title,
        )
        # submit_capture writes notes rows DIRECTLY (bypassing NoteSync), creating
        # and possibly renaming the note within this RPC. The provisional name is
        # never observed by a client before the rename, so emit once on the final
        # resolved name from the return dict. Async path -> publish_snapshot.
        resolved = result.get("note_name")
        if isinstance(resolved, str) and resolved:
            await self.rt.publish_snapshot(Entity.NOTE, resolved)
        return result

    async def ensure_note(self, name: str) -> dict[str, Any]:
        assert self.rt.db is not None
        row = notes_mod.ensure_note(self.rt.db, self.rt.repo_root, name)
        # ensure_note writes the notes row directly (bypassing NoteSync); a new
        # note may have appeared in the active list. Emit key-only via the async
        # choke point.
        await self.rt.publish_snapshot(Entity.NOTE, name)
        return {"name": name, "materialized_path": str(row.get("materialized_path", ""))}

    async def retire_note(self, name: str) -> dict[str, Any]:
        assert self.rt.db is not None
        try:
            dest = notes_mod.retire_note(self.rt.db, self.rt.repo_root, name)
        except Exception as exc:
            raise ValueError(f"could not retire note: {exc}") from exc
        # Retire flips status away from 'active' -> the note drops from the notes
        # snapshot (status='active' filter). Emit so the client refetches and drops it.
        await self.rt.publish_snapshot(Entity.NOTE, name)
        return {"name": name, "dest_name": dest.name}


__all__ = ["NoteOps"]
