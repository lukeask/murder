/**
 * `WorkspaceSlideOverlay` — the workspace slide animation surface (workspaces plan, step 4b).
 *
 * While `workspaceStore.transition` is non-null the {@link ./App.js Shell} renders THIS instead of
 * its normal layout — the same Body-slot-takeover shape as a `fullscreen` mode presentation
 * ({@link ./Overlay.js presentationHidesLayout}), except driven by the transition state rather than
 * the mode stack (a slide is not an input mode; input is blocked wholesale by the dispatcher while
 * the transition is up — see {@link ../hooks/useRootInput.js}).
 *
 * ## How the slide is drawn
 *
 * The switch pipeline has ALREADY committed by the time this mounts (stores hydrated, activeIndex
 * moved) — the slide is pure cosmetics over two captured text frames:
 *
 *  1. The two same-sized frames are vertically concatenated into a `columns × (2·rows)` surface
 *     ({@link ../render/frameText.js concatFrames}; order per direction — `next` puts the outgoing
 *     frame on top, `prev` below).
 *  2. A `setInterval` tick (~toast pattern: timestamp-driven, eased with the toasts' cubic
 *     {@link ./BottomBar.js easeOut}) advances `now`; each render slices a `rows`-tall window at
 *     the eased row offset ({@link ../render/frameText.js sliceFrameWindow}) and paints it as one
 *     raw `<Text wrap="truncate">` row per line — ANSI passes through untouched, the same way the
 *     tmux frame body renders captured terminal output.
 *  3. Every tick forces a genuine full repaint ({@link ../terminal/forceInkRepaint.js}) so
 *     `incrementalRendering`'s line diffing never smears rows that merely shifted vertically.
 *
 * When the eased offset reaches the far edge (or the terminal resizes mid-slide, invalidating the
 * captured frames' geometry) the transition is cleared and one final full repaint paints the real,
 * live view — the frames are only ever pixels, never truth.
 */

import { Box, Text, useStdout } from 'ink';
import { type ReactNode, useEffect, useMemo, useState } from 'react';
import { useInputStores, useWorkspaceStore } from '../hooks/useInputStores.js';
import { useTerminalSize } from '../hooks/useTerminalSize.js';
import type { WorkspaceDirection, WorkspaceTransition } from '../input/workspaceStore.js';
import { concatFrames, sliceFrameWindow, type TextFrame } from '../render/frameText.js';
import { forceInkFullRepaint } from '../terminal/forceInkRepaint.js';
import { easeOut } from './BottomBar.js';

/** Slide duration. TUNED VALUE (step-5 review): 300ms — the middle of the spec's 250–400ms band.
 * Rationale: short enough that blocking all input for the whole transition reads as animation, not
 * latency (< half a second, per the input-blocking spec), yet long enough that the row-quantized
 * motion doesn't look like a single jump-cut on a tall terminal. Paired with the cubic
 * {@link ./BottomBar.js easeOut} (decelerating, NOT ease-in-out) so the target frame *arrives*
 * decisively and settles, rather than easing symmetrically in and out — the right feel for a
 * discrete "snap to the next workspace" gesture. */
export const WORKSPACE_SLIDE_MS = 300;

/** Tick interval. TUNED VALUE (step-5 review): 40ms (~25fps) — inside the spec's 30–50ms band and a
 * touch snappier than the toasts' 50ms cadence. At 300ms that is ~7–8 composited frames; finer ticks
 * buy little because the slide is row-quantized (integer row offsets), while each tick costs a full
 * repaint. Kept a named constant so a future terminal-speed measurement can retune it in one place. */
const SLIDE_TICK_MS = 40;

/**
 * The eased row offset of the `rows`-tall window into the concatenated double-height frame at time
 * `now`. `next` (J) starts on the outgoing frame (offset 0) and slides DOWN to the target (offset
 * `rows`); `prev` (K) is the mirror — the target sits on top, so the window starts at `rows` and
 * slides up to 0. Pure, so the offset math is testable without a render or timers.
 */
export function slideRowOffset(
  direction: WorkspaceDirection,
  startedAt: number,
  now: number,
  rows: number,
): number {
  const progress = easeOut((now - startedAt) / WORKSPACE_SLIDE_MS);
  return direction === 'next' ? Math.round(progress * rows) : Math.round((1 - progress) * rows);
}

/** Whether the slide has run its course at time `now` (the window has reached the target frame). */
export function slideDone(startedAt: number, now: number): boolean {
  return now - startedAt >= WORKSPACE_SLIDE_MS;
}

/** Build the double-height slide surface, or `null` if the frames can't composite (belt-and-braces:
 * capture validates geometry, and the pipeline only starts a slide on matching sizes — but a throw
 * here must degrade to "no surface", not crash the shell mid-switch). */
function buildSlideSurface(transition: WorkspaceTransition): TextFrame | null {
  try {
    return concatFrames(transition.fromFrame, transition.toFrame, transition.direction);
  } catch {
    return null;
  }
}

/**
 * The slide surface. Renders nothing when no transition is up (the Shell shouldn't mount it then,
 * but the guard keeps the component total). Owns the tick loop, the per-tick full repaint, the
 * end-of-slide commit repaint, and the cancel-on-resize rule.
 */
export function WorkspaceSlideOverlay(): ReactNode {
  const transition = useWorkspaceStore((s) => s.transition);
  const { workspace } = useInputStores();
  const { stdout } = useStdout();
  const { columns, rows } = useTerminalSize();
  const [now, setNow] = useState(() => Date.now());

  // The tick loop (toast pattern): a timestamp-driven interval; each tick re-renders with a fresh
  // `now`, and the eased offset is derived from it — no accumulated per-tick state to drift.
  useEffect(() => {
    if (transition === null) {
      return;
    }
    setNow(Date.now());
    const handle = setInterval(() => setNow(Date.now()), SLIDE_TICK_MS);
    return () => clearInterval(handle);
  }, [transition]);

  // Completion + resize cancellation + the per-tick repaint. Runs after every committed tick frame:
  //  - resize mid-slide: the captured frames' geometry is stale → cancel and complete instantly
  //    (the switch already committed; only the animation is abandoned);
  //  - slide done: clear the transition so the Shell swaps the live layout back in;
  //  - otherwise: force a full repaint of the frame just committed, bypassing incremental-rendering
  //    diff artifacts on vertically shifting rows.
  // The end-of-slide repaint is deferred a beat (setTimeout 0) so React has flushed the Shell's
  // real layout before the repaint re-renders from Ink's root — repainting synchronously here would
  // faithfully repaint the overlay we are about to unmount.
  useEffect(() => {
    if (transition === null) {
      return;
    }
    const resized = columns !== transition.fromFrame.columns || rows !== transition.fromFrame.rows;
    if (resized || slideDone(transition.startedAt, now)) {
      workspace.getState().clearTransition();
      const handle = setTimeout(() => forceInkFullRepaint(stdout), 0);
      return () => clearTimeout(handle);
    }
    forceInkFullRepaint(stdout);
    return undefined;
  }, [transition, now, columns, rows, workspace, stdout]);

  const surface = useMemo(
    () => (transition === null ? null : buildSlideSurface(transition)),
    [transition],
  );

  if (transition === null || surface === null) {
    return null;
  }
  const frameRows = transition.fromFrame.rows;
  const offset = slideRowOffset(transition.direction, transition.startedAt, now, frameRows);
  const window = sliceFrameWindow(surface, offset, frameRows);
  const lines = window.text.split('\n');
  return (
    <Box flexDirection="column" width={columns} height={rows} overflow="hidden">
      {lines.map((line, index) => (
        // biome-ignore lint/suspicious/noArrayIndexKey: rows are positional by nature (a fixed grid).
        <Text key={index} wrap="truncate">
          {line === '' ? ' ' : line}
        </Text>
      ))}
    </Box>
  );
}
