/**
 * `useOrientation` — derive a `'portrait' | 'landscape'` layout mode from the live terminal size.
 *
 * The layout shell (Phase 2) flips its Body `flexDirection` on this value: landscape lays the rails
 * and stage out in a `row` (side-by-side), portrait stacks them in a `column` (top-to-bottom). The
 * decision is purely a function of the terminal's `columns`/`rows` aspect ratio — a terminal is
 * "portrait" when it is *taller than it is wide* by more than a comfortable margin.
 *
 * ## Why an aspect THRESHOLD, not `columns < rows`
 *
 * Terminal cells are not square: a character cell is roughly twice as tall as it is wide (~1:2).
 * So a *visually* square terminal has about twice as many columns as rows. Comparing raw
 * `columns < rows` would call almost every normal terminal "portrait". We instead compare against
 * `rows * ASPECT`, where `ASPECT` ≈ the cell height:width ratio, so the split happens at the point
 * where the rendered region actually looks taller than wide.
 *
 * `ASPECT = 2.2` is deliberately a hair above the ~2.0 cell ratio: it biases toward `landscape`
 * (the richer side-by-side layout) so a roughly-square window still gets columns, and only a clearly
 * tall/narrow window (a portrait monitor, a split pane) trips into the stacked layout. Tune here.
 *
 * The pure {@link isPortrait} predicate is exported separately so the boundary is unit-testable
 * without a terminal (the threshold is the load-bearing decision; the hook is thin glue over
 * {@link useTerminalSize}, which already re-renders the shell on resize — rule: no formatting/bus).
 *
 * ## Phase 2/3 handoff
 *  - The Shell calls `useOrientation()` once and threads the value to `<Body>` (flexDirection) and
 *    to each `<Rail orientation paneOrientation>` so a Rail flips its Panes between stack (landscape:
 *    `column`) and side-by-side (portrait: `row`). Pass the same value down — do not call the hook
 *    per-Rail (one source of truth; avoids any divergence mid-tree).
 *  - A resize re-runs {@link useTerminalSize}, which re-renders, which re-evaluates this hook and
 *    re-measures every Pane (focus rects update) — so directional `ctrl+hjkl` adapts to the new
 *    orientation for free, per the spec's focus model.
 */

import { type TerminalSize, useTerminalSize } from './useTerminalSize.js';

export type Orientation = 'portrait' | 'landscape';

/**
 * The cell height:width ratio used to normalise the aspect comparison. A terminal is portrait when
 * its columns fall below `rows * ASPECT` (see the file header for why ~2.2 rather than the bare
 * ~2.0 cell ratio — it biases toward the landscape layout for roughly-square windows).
 */
export const ORIENTATION_ASPECT = 2.2;

/**
 * Pure boundary predicate: `true` when the terminal is "portrait" (taller than wide) given its
 * `columns`/`rows`. Portrait iff `columns < rows * aspect`. Exported for unit tests so the threshold
 * is verifiable without a real terminal; `aspect` defaults to {@link ORIENTATION_ASPECT}.
 */
export function isPortrait(
  columns: number,
  rows: number,
  aspect: number = ORIENTATION_ASPECT,
): boolean {
  return columns < rows * aspect;
}

/** Map a {@link TerminalSize} to its {@link Orientation} via {@link isPortrait}. */
export function orientationFor(size: TerminalSize): Orientation {
  return isPortrait(size.columns, size.rows) ? 'portrait' : 'landscape';
}

/** Live orientation, re-evaluated whenever the terminal resizes (via {@link useTerminalSize}). */
export function useOrientation(): Orientation {
  return orientationFor(useTerminalSize());
}
