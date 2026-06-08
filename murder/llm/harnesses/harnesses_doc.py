"""Generate ``HARNESSES_AND_MODELS.md``.

A planner about to carve/write a ticket needs to know which harnesses are
available, what models each one offers, and which effort levels it supports.
That information is discovered dynamically at startup (see ``model_cache``) and
changes when the user edits settings, so it can't live in a static prompt.

:func:`render_harnesses_doc` is a **pure** function: ``(enabled, models)`` in,
markdown string out. Effort levels are read from each adapter's
``supported_efforts`` classvar (the single source of truth) inside the renderer,
which keeps it deterministic and trivially unit-testable. The file I/O and the
model-cache reads live in :func:`write_harnesses_doc`, called from startup (after
discovery) and after every settings change.
"""

from __future__ import annotations

import logging
from pathlib import Path

from murder.config import Config
from murder.llm.harnesses import REGISTRY
from murder.llm.harnesses.model_cache import get_available_models
from murder.state.storage.paths import harnesses_and_models_md

LOGGER = logging.getLogger(__name__)

# Harness kinds in the order we want them to appear in the doc.
_HARNESS_ORDER: tuple[str, ...] = (
    "claude_code",
    "codex",
    "cursor",
    "pi",
    "antigravity",
    "native_coding_crow",
)


def _enabled_harnesses(repo_root: Path) -> list[str]:
    """The project's enabled crow harnesses, in doc order.

    Mirrors the "ENABLED CROW HARNESSES" set the settings screen edits: the
    ``default_crow`` role's ``harnesses`` pool (falling back to its single
    ``harness``). A disabled harness is omitted from the doc so the planner
    can't assign a ticket to a harness the project isn't set up for. Falls back
    to every registered harness only if config can't be loaded.
    """
    try:
        config = Config.load(repo_root)
        crow = config.default_crow
        pool = list(crow.harnesses) if crow.harnesses else [crow.harness]
    except Exception:
        LOGGER.debug("could not load config for enabled harnesses; listing all", exc_info=True)
        pool = list(REGISTRY)
    enabled = set(pool)
    ordered = [k for k in _HARNESS_ORDER if k in enabled]
    ordered.extend(k for k in pool if k not in _HARNESS_ORDER)
    return ordered


def _supported_efforts(harness: str) -> tuple[str, ...]:
    adapter_cls = REGISTRY.get(harness)
    if adapter_cls is None:
        return ()
    return tuple(adapter_cls.supported_efforts)


def render_harnesses_doc(
    enabled: list[str],
    models: dict[str, list[tuple[str, str]]],
) -> str:
    """Render the harnesses/models/effort doc as markdown.

    *enabled* is the ordered list of harness kinds to list. *models* maps each
    harness kind to its ``(model_id, label)`` pairs. Effort levels are derived
    from the adapter ``supported_efforts`` classvar, not passed in. A harness
    with no models is still listed (with ``(no models discovered)``) so the
    planner knows it exists.
    """
    lines: list[str] = [
        "# Harnesses and models",
        "",
        "Available coding harnesses, their models, and effort levels. Generated",
        "at startup from live model discovery and regenerated on settings change.",
        "When carving/writing a ticket, pick a `harness` + `model` from this list",
        "(and an effort level if the harness supports one).",
        "",
    ]
    for harness in enabled:
        lines.append(f"## {harness}")
        lines.append("")
        harness_models = models.get(harness) or []
        if harness_models:
            lines.append("Models:")
            for model_id, label in harness_models:
                if label and label != model_id:
                    lines.append(f"- `{model_id}` — {label}")
                else:
                    lines.append(f"- `{model_id}`")
        else:
            lines.append("Models: (no models discovered)")
        lines.append("")
        efforts = _supported_efforts(harness)
        if efforts:
            lines.append(f"Effort levels: {', '.join(efforts)}")
        else:
            lines.append("Effort levels: (none)")
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def write_harnesses_doc(repo_root: Path) -> None:
    """Render the doc from the live model cache and write it to ``.murder/``.

    Lists the project's *enabled* crow harnesses and reads each one's models
    through :func:`get_available_models` (discovered cache → classvar fallback),
    so calling this after discovery completes — or after a settings change —
    captures the current enabled set and model lists. Never raises: doc
    generation is best-effort and must not break startup or a settings save.
    """
    try:
        enabled = _enabled_harnesses(repo_root)
        models = {kind: get_available_models(kind) for kind in enabled}
        text = render_harnesses_doc(enabled, models)
        path = harnesses_and_models_md(repo_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except Exception:
        LOGGER.debug("failed to write HARNESSES_AND_MODELS.md", exc_info=True)


__all__ = ["render_harnesses_doc", "write_harnesses_doc"]
