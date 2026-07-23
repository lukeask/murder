"""Note I/O concern extracted from the Orchestrator (move-code refactor)."""

from __future__ import annotations

from typing import Any

from murder.app.service.runtime_scope import OrchestratorHost
from murder.llm.direct import resolve_direct_role_client
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

        client, notetaker_cfg = resolve_direct_role_client(
            self.rt.config.notetaker,
            self.rt.user_cfg,
            "notetaker",
            "notetaker",
        )
        result = await notes_mod.submit_capture(
            repo_root=self.rt.repo_root,
            conn=self.rt.db,
            raw=raw.strip(),
            client=client,
            config=notetaker_cfg,
            note_name=notes_mod.today_name(),
            title=title,
        )
        return result

    async def ensure_note(self, name: str) -> dict[str, Any]:
        assert self.rt.db is not None
        row = notes_mod.ensure_note(self.rt.db, self.rt.repo_root, name)
        return {"name": name, "materialized_path": str(row.get("materialized_path", ""))}

    async def retire_note(self, name: str) -> dict[str, Any]:
        assert self.rt.db is not None
        try:
            dest = notes_mod.retire_note(self.rt.db, self.rt.repo_root, name)
        except Exception as exc:
            raise ValueError(f"could not retire note: {exc}") from exc
        return {"name": name, "dest_name": dest.name}


__all__ = ["NoteOps"]
