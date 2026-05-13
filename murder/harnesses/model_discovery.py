"""Best-effort `/model(s)` discovery for interactive harnesses."""

from __future__ import annotations

import os
import time
from pathlib import Path

from murder import tmux
from murder.harnesses import get as get_harness
from murder.harnesses.models import HarnessStartSpec
from murder.harnesses.results import SimpleResult, fail_result


def _probe_session_name(prefix: str, kind: str) -> str:
    return f"murder_{prefix}_{kind}_{os.getpid()}_{time.monotonic_ns() % 1_000_000}"


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
    session = _probe_session_name("models", kind)
    result: SimpleResult[list[tuple[str, str]]]
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
            result = fail_result(started.message or f"{kind} did not start")
        else:
            result = await harness_session.collect_available_models()
            if not result.ok:
                result = fail_result(
                    f"{kind} /models discovery parse failed: "
                    f"{result.message or 'no model choices parsed'}"
                )
    except Exception as exc:
        result = fail_result(f"{kind} /models discovery probe failed: {exc}")

    try:
        await tmux.kill_session(session)
    except Exception as exc:
        if result.ok:
            return fail_result(
                f"{kind} /models discovery cleanup failed for {session}: {exc}"
            )
        return fail_result(
            f"{result.message}; cleanup also failed for {session}: {exc}"
        )
    return result


async def probe_invalid_harness_model(
    kind: str,
    repo_root: Path,
    *,
    model: str = "thisisnotarealmodel",
    ready_timeout_s: float = 45.0,
) -> SimpleResult[None]:
    """Start a temporary harness and verify `/model <model>` is rejected."""
    adapter = get_harness(kind)
    session = _probe_session_name("invalid_model", kind)
    result: SimpleResult[None]
    try:
        harness_session = adapter.attach(session, repo_root)
        started = await harness_session.start(
            HarnessStartSpec(
                cwd=repo_root,
                ready_timeout_s=ready_timeout_s,
            )
        )
        if not started.ok:
            result = fail_result(started.message or f"{kind} did not start")
        else:
            result = await harness_session.probe_invalid_model(model)
            if not result.ok:
                result = fail_result(
                    f"{kind} invalid model probe rejection detection failed: "
                    f"{result.message or 'no rejection detected'}"
                )
    except Exception as exc:
        result = fail_result(f"{kind} invalid model probe failed: {exc}")

    try:
        await tmux.kill_session(session)
    except Exception as exc:
        if result.ok:
            return fail_result(
                f"{kind} invalid model probe cleanup failed for {session}: {exc}"
            )
        return fail_result(
            f"{result.message}; cleanup also failed for {session}: {exc}"
        )
    return result
