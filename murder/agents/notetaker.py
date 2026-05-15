"""Compat shim: interactive NotetakerAgent was replaced by capture submit (``notetaker_capture``).

The class name stays importable so ``murder.agents`` exports remain stable. Do not instantiate.
"""


class NotetakerAgent:  # noqa: D401 — imperative phrasing intentional
    """Removed; planning capture now uses bus command ``notetaker.capture.submit``."""

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise RuntimeError(
            "NotetakerAgent was removed; use the orchestrator worker kind "
            "'notetaker.capture.submit' (see murder.notetaker_capture)."
        )
