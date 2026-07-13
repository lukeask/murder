"""Read-only tmux frame source for verified controller observation loops."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from murder.llm.harness_control.model.evidence import FrameId, HarnessId, TerminalFrame
from murder.runtime.terminal import tmux


class TmuxFrameObserver:
    """Captures immutable frames with dimensions and ANSI provenance.

    This source is deliberately not an actuator and exposes no input methods.
    A new instance/pane epoch must be used after a terminal reattachment or
    recreation, making observation revisions unambiguous across session lives.
    """

    def __init__(
        self,
        session: str,
        harness_id: HarnessId,
        *,
        pane_epoch: int = 0,
        capture_sequence: int = 0,
        lines: int = 300,
        preserve_ansi: bool = True,
    ) -> None:
        if pane_epoch < 0 or capture_sequence < 0:
            raise ValueError("pane epoch and capture sequence cannot be negative")
        self._session = session
        self._harness_id = harness_id
        self._pane_epoch = pane_epoch
        self._lines = lines
        self._preserve_ansi = preserve_ansi
        self._capture_sequence = capture_sequence

    async def capture_frame(self) -> TerminalFrame:
        width, height = await tmux.pane_dimensions(self._session)
        raw_text = await tmux.capture_pane(
            self._session,
            lines=self._lines,
            escapes=self._preserve_ansi,
        )
        self._capture_sequence += 1
        return TerminalFrame(
            frame_id=FrameId(str(uuid4())),
            harness_id=self._harness_id,
            captured_at=datetime.now(timezone.utc),
            width=width,
            height=height,
            raw_text=raw_text,
            ansi_preserved=self._preserve_ansi,
            pane_epoch=self._pane_epoch,
            capture_sequence=self._capture_sequence,
        )


__all__ = ["TmuxFrameObserver"]
