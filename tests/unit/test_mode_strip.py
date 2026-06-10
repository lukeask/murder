"""ModeStrip scheduler-mode formatting.

refresh_from_snapshot has real transformation logic: it maps the raw
scheduler_mode key to a human label (_MODE_LABELS) and appends the rationale as
a second line when present.  We exercise that via the public method and observe
the string pushed to the widget's output surface (update), not the rendered
frame.
"""

from __future__ import annotations

from murder.app.tui.dispatch.mode_strip import ModeStrip
from tests.support.factories import factory_schedule_snapshot


def test_refresh_maps_mode_key_to_label_and_appends_rationale() -> None:
    strip = ModeStrip()
    pushed: list[str] = []
    strip.update = pushed.append  # type: ignore[method-assign]

    strip.refresh_from_snapshot(
        factory_schedule_snapshot(
            scheduler_mode="crow_magic",
            mode_rationale="waiting for claude quota reset",
        )
    )

    assert pushed
    rendered = pushed[-1]
    # Raw key "crow_magic" is mapped to its display label.
    assert "Crow Magic" in rendered
    # Rationale is appended on its own line.
    assert "waiting for claude quota reset" in rendered
    assert "\n" in rendered
