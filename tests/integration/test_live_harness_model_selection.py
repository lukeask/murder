from __future__ import annotations

import asyncio
import os
import random
import shutil
from pathlib import Path

import pytest

from murder.llm.harnesses import get as get_harness
from murder.llm.harnesses.models import HarnessStartSpec
from murder.runtime.terminal import tmux

_LIVE_ENV = "MURDER_LIVE_MODEL_SELECT"
_HARNESS_CONFIG: dict[str, dict[str, object]] = {
    "claude_code": {"binary": "claude"},
    "codex": {"binary": "codex"},
    "cursor": {
        "binary": "agent",
        "preferred_models": ["composer-2.5", "gpt-5.5"],
    },
    "pi": {"binary": "pi"},
    "antigravity": {
        "binary": "agy",
        "preferred_models": ["gemini-3-1-pro"],
    },
}
_HARNESSES = list(_HARNESS_CONFIG)


def _skip_unless_live(harness: str) -> None:
    if os.environ.get(_LIVE_ENV) != "1":
        pytest.skip(f"set {_LIVE_ENV}=1 to run live harness model-selection tests")
    binary = str(_HARNESS_CONFIG[harness]["binary"])
    if shutil.which(binary) is None:
        pytest.skip(f"{binary!r} is not installed")
    if shutil.which("tmux") is None:
        pytest.skip("'tmux' is not installed")


def _pick_models(
    harness: str,
    discovered: list[tuple[str, str]],
    rng: random.Random,
) -> list[str]:
    cfg = _HARNESS_CONFIG[harness]
    preferred = cfg.get("preferred_models")
    if isinstance(preferred, list):
        available = {model_id for model_id, _ in discovered}
        picks = [model for model in preferred if model in available]
        if picks:
            return rng.sample(picks, k=min(2, len(picks)))
    models = [model_id for model_id, _ in discovered]
    return rng.sample(models, k=min(2, len(models)))


@pytest.mark.integration
@pytest.mark.parametrize("harness", _HARNESSES)
def test_live_harness_model_selection_roundtrip(harness: str, tmp_path: Path) -> None:
    _skip_unless_live(harness)
    asyncio.run(_run_roundtrip(harness, tmp_path))


async def _run_roundtrip(harness: str, repo_root: Path) -> None:
    seed = f"{harness}-model-selection"
    rng = random.Random(seed)
    probe_session = f"murder_test_models_{harness}_{rng.randrange(1_000_000):06d}"
    adapter = get_harness(harness)

    try:
        probe = adapter.attach(probe_session, repo_root)
        started = await probe.start(
            HarnessStartSpec(cwd=repo_root, ready_timeout_s=90.0, poll_interval_s=0.5)
        )
        assert started.ok, f"{harness}: startup failed before model discovery: {started.message}"

        discovered = await probe.collect_available_models()
        assert discovered.ok, f"{harness}: available-model discovery failed: {discovered.message}"
        assert discovered.data, f"{harness}: available-model discovery returned no rows"
    finally:
        await tmux.kill_session(probe_session)

    selected_models = _pick_models(harness, discovered.data, rng)
    efforts = list(adapter.supported_efforts) or [None]
    selected_efforts = rng.sample(efforts, k=min(2, len(efforts)))

    for model in selected_models:
        for effort in selected_efforts:
            session = f"murder_test_select_{harness}_{rng.randrange(1_000_000):06d}"
            try:
                candidate = get_harness(harness, startup_model=model, startup_effort=effort)
                harness_session = candidate.attach(session, repo_root)
                started = await harness_session.start(
                    HarnessStartSpec(
                        cwd=repo_root,
                        startup_model=model,
                        startup_effort=effort,
                        ready_timeout_s=90.0,
                        poll_interval_s=0.5,
                    )
                )
                label = f"{harness} model={model!r} effort={effort!r}"
                assert started.ok, f"{label}: startup/model selection failed: {started.message}"

                active = await harness_session.collect_active_model_state()
                assert active.ok, f"{label}: active model parse failed: {active.message}"
                assert active.data is not None
                assert active.data.model == model, f"{label}: active state was {active.data}"
                if effort is not None:
                    assert active.data.effort == effort, f"{label}: active state was {active.data}"
            finally:
                await tmux.kill_session(session)
