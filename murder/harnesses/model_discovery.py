"""Best-effort `/model(s)` discovery for interactive harnesses."""

from __future__ import annotations

import os
import time
from pathlib import Path

from murder import tmux
from murder.harnesses import get as get_harness
from murder.harnesses.models import HarnessStartSpec
from murder.harnesses.results import SimpleResult, fail_result


async def discover_harness_models(
    kind: str,
    repo_root: Path,
    *,
    startup_model: str | None = None,
    ready_timeout_s: float = 45.0,
) -> SimpleResult[list[tuple[str, str]]]:
    """Start a temporary harness session and collect choices from `/model(s)`.

    Harnesses whose model picker isn't machine-parsable set
    ``model_list_command = None``; for those the hardcoded
    ``available_startup_models`` is authoritative and we skip the session spin-up.
    """
    adapter = get_harness(kind, startup_model=startup_model)
    if adapter.model_list_command is None:
        return fail_result(f"{kind} has no machine-parsable model list")
    session = f"murder_models_{kind}_{os.getpid()}_{time.monotonic_ns() % 1_000_000}"
    try:
        harness_session = adapter.attach(session, repo_root)
        started = await harness_session.start(
            HarnessStartSpec(
                cwd=repo_root,
                startup_model=startup_model,
                ready_timeout_s=ready_timeout_s,
            )
        )
        if not started.ok:
            return fail_result(started.message or f"{kind} did not start")
        return await harness_session.collect_available_models()
    except Exception as exc:
        return fail_result(f"{kind} /models discovery failed: {exc}")
    finally:
        try:
            await tmux.kill_session(session)
        except Exception:
            pass
