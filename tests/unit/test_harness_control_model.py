from __future__ import annotations

from datetime import datetime, timezone

import pytest

from murder.llm.harness_control.model import (
    EvidenceId,
    EvidenceRef,
    FrameId,
    HarnessId,
    Knowledge,
    ObservationDelta,
    ObservationRevision,
    Observed,
    TerminalFrame,
    unknown_snapshot,
)
from murder.llm.harness_control.runtime.observer import ObservationStore


def test_present_observation_requires_value() -> None:
    now = datetime.now(timezone.utc)
    revision = ObservationRevision(1, 2, 3)
    with pytest.raises(ValueError, match="only PRESENT"):
        Observed(Knowledge.PRESENT, None, (), now, revision)
    with pytest.raises(ValueError, match="only PRESENT"):
        Observed(Knowledge.UNKNOWN, "not allowed", (), now, revision)


def test_unknown_snapshot_does_not_confuse_unseen_with_absent() -> None:
    now = datetime.now(timezone.utc)
    snapshot = unknown_snapshot(HarnessId("codex"), captured_at=now)
    assert snapshot.composer.knowledge is Knowledge.UNKNOWN
    assert snapshot.active_model.knowledge is Knowledge.UNKNOWN
    assert snapshot.question.knowledge is Knowledge.UNKNOWN

    store = ObservationStore(snapshot)
    same_meaning_new_capture = Observed.without_value(
        Knowledge.UNKNOWN,
        evidence=(EvidenceRef(EvidenceId("e-2"), FrameId("f-2")),),
        observed_at=now,
        revision=ObservationRevision(0, 2, 99),
    )
    refreshed = store.apply(
        ObservationDelta({"composer": same_meaning_new_capture}),
        captured_at=now,
        pane_epoch=0,
        capture_sequence=2,
    )
    assert refreshed.revision.capture_sequence == 2  # noqa: PLR2004
    assert refreshed.revision.semantic_sequence == snapshot.revision.semantic_sequence


def test_terminal_frame_preserves_capture_provenance() -> None:
    frame = TerminalFrame(
        FrameId("frame-1"),
        HarnessId("pi"),
        datetime.now(timezone.utc),
        220,
        50,
        "\x1b[1mraw\x1b[0m",
        True,
        2,
        4,
    )
    assert frame.ansi_preserved
    assert frame.pane_epoch == frame.capture_sequence - 2
    assert frame.raw_text.endswith("\x1b[0m")
