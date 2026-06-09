from __future__ import annotations

import asyncio
from pathlib import Path

from murder.llm.harnesses.model_discovery import discover_harness_models
from murder.llm.harnesses.results import ok_result


def test_model_discovery_kills_probe_session_when_cancelled(monkeypatch, tmp_path: Path) -> None:
    kills: list[str] = []
    started = asyncio.Event()

    class _HungSession:
        async def start(self, _spec):
            started.set()
            await asyncio.Event().wait()
            return ok_result()

        async def collect_available_models(self):
            return ok_result([("model", "Model")])

    class _Adapter:
        def attach(self, session: str, repo_root: Path):
            del session, repo_root
            return _HungSession()

    async def _kill_session(name: str) -> None:
        kills.append(name)

    monkeypatch.setattr("murder.llm.harnesses.model_discovery.get_harness", lambda *_a, **_kw: _Adapter())
    monkeypatch.setattr("murder.llm.harnesses.model_discovery.tmux.kill_session", _kill_session)

    async def _run() -> None:
        task = asyncio.create_task(discover_harness_models("codex", tmp_path))
        await started.wait()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await asyncio.sleep(0)

    asyncio.run(_run())

    assert len(kills) == 1
    assert kills[0].startswith("murder_models_codex_")
