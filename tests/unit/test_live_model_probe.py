"""Codex model discovery uses the app-server catalog, not TUI or static rows."""

from __future__ import annotations

import asyncio
from pathlib import Path

from murder.llm.harness_control.runtime.live_model_probe import _probe_codex_app_server_models


def test_codex_app_server_probe_collects_all_visible_pages(monkeypatch, tmp_path: Path) -> None:
    calls: list[dict[str, object]] = []

    class FakeConnection:
        def __init__(self, **kwargs: object) -> None:
            calls.append(kwargs)

        async def start(self) -> None:
            return None

        async def aclose(self) -> None:
            return None

    class FakeClient:
        def __init__(self, _connection: FakeConnection) -> None:
            pass

        async def initialize(self) -> dict[str, object]:
            return {}

        async def model_list(self, *, cursor=None):
            if cursor is None:
                return {
                    "data": [
                        {"id": "gpt-current", "displayName": "GPT Current", "hidden": False},
                        {"id": "hidden", "displayName": "Hidden", "hidden": True},
                    ],
                    "nextCursor": "next",
                }
            return {"data": [{"id": "gpt-next", "hidden": False}], "nextCursor": None}

    monkeypatch.setattr(
        "murder.llm.harness_control.runtime.live_model_probe.AppServerConnection", FakeConnection
    )
    monkeypatch.setattr(
        "murder.llm.harness_control.runtime.live_model_probe.AppServerClient", FakeClient
    )

    result = asyncio.run(_probe_codex_app_server_models(tmp_path, timeout_s=2))

    assert result.ok
    assert result.models == (("gpt-current", "GPT Current"), ("gpt-next", "gpt-next"))
    assert calls == [{"cwd": str(tmp_path), "request_timeout_s": 2}]
