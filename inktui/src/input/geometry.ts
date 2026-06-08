/**
 * Directional-focus geometry — the `_directional_focus_target` kernel from the Textual app
 * (`murder/app/tui/app.py`), ported as a **pure function over measured rects**.
 *
 * Why a pure fn and not a method on the focus store: the original lived on the 2200-line
 * `MurderApp` and read `widget.region` straight off live Textual widgets, which made it
 * untestable without a running app. Here the rects are *data* — Ink's `measureElement` produces
 * them at the component layer (C5) and hands them in. The kernel only does arithmetic on
 * rectangles, so it unit-tests with hand-written rects and never imports React, Ink, or any
 * store (rule 5: input/focus is data, not gating; this is the data half).
 *
 * The scoring is a faithful port: among candidates strictly *in* the requested direction
 * (positive primary gap), prefer the nearest by primary gap, then ones that overlap on the
 * cross axis, then the smallest cross-axis gap, then the largest overlap, then declaration
 * order as the final stable tiebreak. See {@link directionalFocusTarget} for the per-field map.
 */

/** A measured rectangle in terminal cells. `x`/`y` are the top-left; `width`/`height` extend
 * right/down. Matches what Ink `measureElement` yields, reduced to the four numbers the kernel
 * needs — no widget handle leaks in, keeping the kernel pure. */
export interface Rect {
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
}

/** A focus candidate: an opaque id plus where it is on screen. The kernel returns the winning
 * `id`; the caller (focus store) maps it back to a focus target. Generic over the id type so the
 * same kernel serves `FocusId` candidates without the geometry module depending on focus types. */
export interface FocusCandidate<Id> {
  readonly id: Id;
  readonly rect: Rect;
}

/** The four navigation directions `ctrl+h/j/k/l` map to. A string union (not an enum) so it is
 * trivially serialisable and matches the keymap-as-data intents the dispatcher emits. */
export type Direction = 'left' | 'right' | 'up' | 'down';

/** Right edge (exclusive), mirroring Textual `region.right`. */
function right(rect: Rect): number {
  return rect.x + rect.width;
}

/** Bottom edge (exclusive), mirroring Textual `region.bottom`. */
function bottom(rect: Rect): number {
  return rect.y + rect.height;
}

/** Length of the overlap of two 1-D intervals, clamped at 0 (no overlap). Static helper ported
 * 1:1 from the Python `_axis_overlap`. */
function axisOverlap(aStart: number, aEnd: number, bStart: number, bEnd: number): number {
  return Math.max(0, Math.min(aEnd, bEnd) - Math.max(aStart, bStart));
}

/** The per-candidate score components for one direction. `primaryGap < 0` means the candidate is
 * behind the source on the travel axis → not a target. Kept as a struct so the direction switch
 * computes geometry once and the scoring/comparison is shared. */
interface Measure {
  readonly primaryGap: number;
  readonly overlap: number;
  readonly secondaryGap: number;
}

/** Compute a candidate's gap/overlap relative to the source for the travel direction. This is the
 * branch the Python kernel inlined four times; factored to one place, parameterised by direction,
 * so the (identical) scoring below is written once. */
function measure(direction: Direction, source: Rect, candidate: Rect): Measure {
  switch (direction) {
    case 'right': {
      const overlap = axisOverlap(source.y, bottom(source), candidate.y, bottom(candidate));
      return {
        primaryGap: candidate.x - right(source),
        overlap,
        secondaryGap:
          overlap > 0
            ? 0
            : Math.min(
                Math.abs(source.y - bottom(candidate)),
                Math.abs(candidate.y - bottom(source)),
              ),
      };
    }
    case 'left': {
      const overlap = axisOverlap(source.y, bottom(source), candidate.y, bottom(candidate));
      return {
        primaryGap: source.x - right(candidate),
        overlap,
        secondaryGap:
          overlap > 0
            ? 0
            : Math.min(
                Math.abs(source.y - bottom(candidate)),
                Math.abs(candidate.y - bottom(source)),
              ),
      };
    }
    case 'down': {
      const overlap = axisOverlap(source.x, right(source), candidate.x, right(candidate));
      return {
        primaryGap: candidate.y - bottom(source),
        overlap,
        secondaryGap:
          overlap > 0
            ? 0
            : Math.min(
                Math.abs(source.x - right(candidate)),
                Math.abs(candidate.x - right(source)),
              ),
      };
    }
    case 'up': {
      const overlap = axisOverlap(source.x, right(source), candidate.x, right(candidate));
      return {
        primaryGap: source.y - bottom(candidate),
        overlap,
        secondaryGap:
          overlap > 0
            ? 0
            : Math.min(
                Math.abs(source.x - right(candidate)),
                Math.abs(candidate.x - right(source)),
              ),
      };
    }
  }
}

/** Lexicographic comparison of two score tuples; `< 0` means `a` is the better (closer) target.
 * Tuple order mirrors the Python `score` tuple exactly:
 *   1. primary gap (nearest in the travel direction wins),
 *   2. cross-axis overlap presence (0 = overlapping, 1 = not — overlapping wins),
 *   3. secondary (cross-axis) gap (smaller wins),
 *   4. negative overlap (more overlap wins),
 *   5. declaration index (earlier wins — the stable final tiebreak). */
function scoreLessThan(a: ScoredCandidate, b: ScoredCandidate): boolean {
  if (a.primaryGap !== b.primaryGap) return a.primaryGap < b.primaryGap;
  if (a.overlapPenalty !== b.overlapPenalty) return a.overlapPenalty < b.overlapPenalty;
  if (a.secondaryGap !== b.secondaryGap) return a.secondaryGap < b.secondaryGap;
  if (a.negOverlap !== b.negOverlap) return a.negOverlap < b.negOverlap;
  return a.index < b.index;
}

/** A candidate reduced to its comparable score tuple plus its declaration index. */
interface ScoredCandidate {
  readonly primaryGap: number;
  readonly overlapPenalty: 0 | 1;
  readonly secondaryGap: number;
  readonly negOverlap: number;
  readonly index: number;
}

/**
 * Pick the focus target in `direction` from `current`, among `candidates`, by the ported scoring.
 * Returns the winning candidate's `id`, or `null` when nothing lies in that direction (the edge
 * of the layout — the caller leaves focus where it is).
 *
 * Pure: same rects in, same id out. `current` must be one of `candidates` by id; candidates with a
 * negative primary gap (behind the source on the travel axis) are skipped, exactly as the original
 * `if primary_gap < 0: continue`. Declaration order of `candidates` is the final tiebreak, so the
 * caller passes them in a stable order (the focus store uses panel screen-position order).
 */
export function directionalFocusTarget<Id>(
  direction: Direction,
  current: Id,
  candidates: readonly FocusCandidate<Id>[],
): Id | null {
  const source = candidates.find((c) => c.id === current);
  if (source === undefined) {
    return null;
  }

  let best: ScoredCandidate | null = null;
  let bestId: Id | null = null;
  candidates.forEach((candidate, index) => {
    if (candidate.id === current) {
      return;
    }
    const m = measure(direction, source.rect, candidate.rect);
    if (m.primaryGap < 0) {
      return;
    }
    const scored: ScoredCandidate = {
      primaryGap: m.primaryGap,
      overlapPenalty: m.overlap > 0 ? 0 : 1,
      secondaryGap: m.secondaryGap,
      negOverlap: -m.overlap,
      index,
    };
    if (best === null || scoreLessThan(scored, best)) {
      best = scored;
      bestId = candidate.id;
    }
  });
  return bestId;
}
