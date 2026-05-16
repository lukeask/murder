from __future__ import annotations

import asyncio
import json
import shlex
import sqlite3
import subprocess
import time
from pathlib import Path
from uuid import uuid4

from murder import db as dbmod
from murder import notes as notes_mod
from murder.bus.client import SocketBusClient
from murder.bus.protocol import ClientKind
from murder.config import Config
from murder.plans.sync import choose_editor
from murder.storage.paths import db_path, note_md

COMMAND_POLL_S = 0.05


class TuiRuntimeClient:
    """TUI-facing service facade.

    This is intentionally narrower than Runtime: reads are local DB snapshots
    for the current transitional widgets, while mutations go through the
    supervisor command RPC surface.
    """

    def __init__(
        self,
        repo_root: Path,
        socket_path: Path,
        config: Config,
        *,
        client_kind: ClientKind = ClientKind.TUI,
    ) -> None:
        self.repo_root = repo_root
        self.config = config
        self.bus = SocketBusClient(
            socket_path,
            client_kind=client_kind,
            client_id=f"{client_kind.value}-{uuid4().hex}",
        )
        self.db = dbmod.connect(db_path(repo_root))
        self.run_id: str | None = None
        self.note_sync = None

    async def connect(self) -> None:
        reply = await self.bus.request("health.ping", {}, timeout_s=5.0)
        self.run_id = str(reply.get("run_id") or "")

    async def close(self) -> None:
        self.db.close()

    async def reconcile_plan(self, name: str) -> None:
        del name
        return None

    def plan_path_for(self, name: str) -> Path:
        row = dbmod.get_plan_row(self.db, name)
        return (
            self.repo_root / row["materialized_path"]
            if row
            else self.repo_root / ".murder" / "plans" / f"{name}.md"
        )

    def note_path_for(self, name: str) -> Path:
        notes_mod.ensure_note(self.db, self.repo_root, name)
        return note_md(self.repo_root, name)

    def open_editor_blocking(self, path: Path, preferred_editor: str | None = None) -> int:
        editor = choose_editor(preferred_editor)
        argv = shlex.split(editor) or ["vi"]
        proc = subprocess.run([*argv, str(path)], check=False)
        return int(proc.returncode)

    async def submit_command(
        self,
        *,
        target_worker: str,
        kind: str,
        payload: dict[str, object],
        timeout_s: float,
    ) -> dict[str, object]:
        submitted = await self.bus.request(
            "command.submit",
            {
                "target_worker": target_worker,
                "kind": kind,
                "payload": payload,
                "agent_id": "tui",
                "correlation_id": f"tui-{uuid4()}",
                "idempotency_key": f"tui-{kind}-{uuid4()}",
            },
            timeout_s=min(timeout_s, 10.0),
        )
        command_id = str(submitted.get("command_id") or "")
        if not command_id:
            raise RuntimeError("command.submit did not return command_id")
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            status = await self.bus.request(
                "command.status",
                {"command_id": command_id},
                timeout_s=min(timeout_s, 10.0),
            )
            state = str(status.get("status") or "")
            if state == "done":
                raw = status.get("result_json")
                return json.loads(str(raw)) if raw else {}
            if state == "failed":
                raise RuntimeError(str(status.get("last_error") or f"{kind} failed"))
            await asyncio.sleep(COMMAND_POLL_S)
        raise TimeoutError(f"{kind} timed out")
