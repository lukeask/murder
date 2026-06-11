/**
 * Budget-engine tests (L1) — the pure layout math, the layer the flex-blind suite can actually
 * guard. These assert the algorithm's CONTRACT, not the rendered layout (Yoga never runs here):
 *  - the Stage hard floor (≥60%) holds on BOTH axes across many cols/rows;
 *  - rails get ≤ their natural size when there's slack;
 *  - compression + the MIN_PANEL_WIDTH clamp engage when tight;
 *  - the math is total — never negative/NaN down to the smallest legible form;
 *  - the usage INNER-width derivation (rail − chrome; portrait splits the strip with crows);
 *  - clipName / FILENAME_CAP bound a rail's width contribution.
 */

import { describe, expect, it } from 'vitest';
import {
  type BodyLayoutInput,
  clipName,
  computeBodyLayout,
  FILENAME_CAP,
  MIN_PANEL_WIDTH,
  MIN_PORTRAIT_RAIL_HEIGHT,
  MIN_USAGE_WIDTH,
  type RailContent,
  STAGE_MIN_FRACTION,
  USAGE_NATURAL_INNER_WIDTH,
  USAGE_PANE_CHROME,
} from '../../src/layout/budget.js';

/**
 * A present rail of the given natural width. `naturalHeight` defaults to `naturalWidth` for the
 * landscape sweeps (where it must be IGNORED — see {@link landscapeIgnoresHeight}); portrait tests
 * pass it explicitly. The two fields are independent inputs (width = landscape, height = portrait).
 */
function rail(naturalWidth: number, naturalHeight = naturalWidth): RailContent {
  return { naturalWidth, naturalHeight, present: true };
}

/** An absent (collapsed) rail — contributes neither cells nor a gap, on either axis. */
const ABSENT: RailContent = { naturalWidth: 0, naturalHeight: 0, present: false };

/** Build a landscape input with the given cols + rail contents (rows fixed, gap 1). */
function landscape(cols: number, left: RailContent, right: RailContent, gap = 1): BodyLayoutInput {
  return { cols, rows: 24, orientation: 'landscape', gap, left, right };
}

/** Build a portrait input with the given rows + rail contents (cols fixed, gap 1). */
function portrait(rows: number, left: RailContent, right: RailContent, gap = 1): BodyLayoutInput {
  return { cols: 40, rows, orientation: 'portrait', gap, left, right };
}

/** A present rail with the given portrait natural HEIGHT (and a distinct width to prove portrait
 * budgets HEIGHT, not width). */
function railH(naturalHeight: number, naturalWidth = 999): RailContent {
  return { naturalWidth, naturalHeight, present: true };
}

describe('computeBodyLayout — Stage hard floor (R3/R4)', () => {
  it('keeps the Stage ≥ 60% of columns across many landscape widths', () => {
    // Sweep a wide range of widths and natural rail sizes; the floor must never break.
    for (let cols = 20; cols <= 400; cols += 7) {
      for (const natural of [0, 5, 12, 30, 80, 200]) {
        const layout = computeBodyLayout(landscape(cols, rail(natural), rail(natural)));
        const floor = Math.ceil(STAGE_MIN_FRACTION * cols);
        expect(layout.stageCells).toBeGreaterThanOrEqual(floor);
        expect(layout.axis).toBe('width');
      }
    }
  });

  it('keeps the Stage ≥ 60% of rows across many portrait heights', () => {
    for (let rows = 12; rows <= 200; rows += 5) {
      for (const natural of [0, 5, 12, 30, 80]) {
        const layout = computeBodyLayout(portrait(rows, rail(natural), rail(natural)));
        const floor = Math.ceil(STAGE_MIN_FRACTION * rows);
        expect(layout.stageCells).toBeGreaterThanOrEqual(floor);
        expect(layout.axis).toBe('height');
      }
    }
  });

  it('the regions + gaps never exceed the total (no overdraw)', () => {
    for (let cols = 20; cols <= 300; cols += 11) {
      const layout = computeBodyLayout(landscape(cols, rail(40), rail(30)));
      const used =
        layout.leftRailCells + layout.rightRailCells + layout.stageCells + 2 /* two gaps */;
      // The Stage absorbs the remainder, so used == total exactly (both rails present → 2 gaps).
      expect(used).toBeLessThanOrEqual(cols);
    }
  });
});

describe('computeBodyLayout — slack: rails at natural size (R1/R2)', () => {
  it('gives each rail its natural size when the budget is roomy', () => {
    // 200 cols, floor=120, gaps=2 → railBudget=78; naturals 24+18=42 < 78 → both natural.
    const layout = computeBodyLayout(landscape(200, rail(24), rail(18)));
    expect(layout.leftRailCells).toBe(24);
    expect(layout.rightRailCells).toBe(18);
    // Stage gets everything left over — comfortably above the 120 floor.
    expect(layout.stageCells).toBe(200 - 24 - 18 - 2);
    expect(layout.stageCells).toBeGreaterThan(Math.ceil(0.6 * 200));
  });

  it('never grows a rail beyond its natural size even with huge slack', () => {
    const layout = computeBodyLayout(landscape(400, rail(10), rail(8)));
    expect(layout.leftRailCells).toBe(10);
    expect(layout.rightRailCells).toBe(8);
  });

  it('a collapsed rail contributes nothing and no gap', () => {
    // Only the left rail present → 1 gap. Right rail is 0 cells.
    const layout = computeBodyLayout(landscape(120, rail(20), ABSENT));
    expect(layout.rightRailCells).toBe(0);
    expect(layout.leftRailCells).toBe(20);
    expect(layout.stageCells).toBe(120 - 20 - 1 /* one gap */);
  });

  it('both rails absent → Stage takes the whole width, no gaps', () => {
    const layout = computeBodyLayout(landscape(100, ABSENT, ABSENT));
    expect(layout.leftRailCells).toBe(0);
    expect(layout.rightRailCells).toBe(0);
    expect(layout.stageCells).toBe(100);
  });
});

describe('computeBodyLayout — compression + min clamp (R3)', () => {
  it('compresses both rails to the budget when naturals exceed it', () => {
    // 100 cols, floor=60, gaps=2 → railBudget=38. Naturals 40+40=80 > 38 → compress.
    const layout = computeBodyLayout(landscape(100, rail(40), rail(40)));
    expect(layout.leftRailCells + layout.rightRailCells).toBeLessThanOrEqual(38);
    // Stage floor still holds.
    expect(layout.stageCells).toBeGreaterThanOrEqual(60);
    // Both rails stay at least their min (the budget seats two 12-cell minimums: 24 ≤ 38).
    expect(layout.leftRailCells).toBeGreaterThanOrEqual(MIN_PANEL_WIDTH);
    expect(layout.rightRailCells).toBeGreaterThanOrEqual(MIN_PANEL_WIDTH);
  });

  it('honours MIN_PANEL_WIDTH at a mid-tight size that seats the minimums but not the naturals', () => {
    // railBudget exactly fits the two minimums (24) with a little slack → both ≥ min, neither natural.
    const cols = 90; // floor=54, gaps=2 → railBudget=34; min sum 24 ≤ 34 < natural sum 80.
    const layout = computeBodyLayout(landscape(cols, rail(40), rail(40)));
    expect(layout.leftRailCells).toBeGreaterThanOrEqual(MIN_PANEL_WIDTH);
    expect(layout.rightRailCells).toBeGreaterThanOrEqual(MIN_PANEL_WIDTH);
    expect(layout.leftRailCells).toBeLessThan(40);
    expect(layout.rightRailCells).toBeLessThan(40);
    expect(layout.stageCells).toBeGreaterThanOrEqual(Math.ceil(0.6 * cols));
  });

  it('the Stage floor WINS over the minimums when the budget cannot seat both (left yields first)', () => {
    // Tiny width: railBudget < two minimums (24). The Stage floor must hold; the LEFT rail yields
    // toward 0 first (lower priority), the right rail keeps as much of its min as fits.
    const cols = 40; // floor=24, gaps=2 → railBudget=14 < 24 (two mins).
    const layout = computeBodyLayout(landscape(cols, rail(40), rail(40)));
    expect(layout.stageCells).toBeGreaterThanOrEqual(Math.ceil(0.6 * cols)); // floor wins
    expect(layout.leftRailCells).toBeLessThanOrEqual(layout.rightRailCells); // left yields first
    // Right rail keeps its min (12) since the budget (14) seats one min; left gets the remaining 2.
    expect(layout.rightRailCells).toBe(MIN_PANEL_WIDTH);
    expect(layout.leftRailCells).toBe(14 - MIN_PANEL_WIDTH);
  });

  it('proportional compression favours the wider rail', () => {
    // railBudget seats both mins with slack; the slack is split proportional to natural size, so the
    // rail with the larger natural width ends up wider after compression.
    const layout = computeBodyLayout(landscape(110, rail(60), rail(20)));
    // 110: floor=66, gaps=2 → railBudget=42; mins 12+12=24 ≤ 42; naturals 80 > 42 → compress.
    expect(layout.leftRailCells).toBeGreaterThan(layout.rightRailCells);
    expect(layout.leftRailCells + layout.rightRailCells).toBeLessThanOrEqual(42);
  });
});

describe('computeBodyLayout — totality (no NaN / negative, smallest forms)', () => {
  it('produces finite, non-negative integer cells at every size from tiny up', () => {
    for (let total = 1; total <= 300; total++) {
      for (const orient of ['landscape', 'portrait'] as const) {
        const input: BodyLayoutInput =
          orient === 'landscape'
            ? landscape(total, rail(40), rail(30))
            : portrait(total, rail(40), rail(30));
        const layout = computeBodyLayout(input);
        for (const n of [layout.leftRailCells, layout.rightRailCells, layout.stageCells]) {
          expect(Number.isInteger(n)).toBe(true);
          expect(n).toBeGreaterThanOrEqual(0);
          expect(Number.isNaN(n)).toBe(false);
        }
      }
    }
  });

  it('handles a zero-natural present rail without dividing by zero', () => {
    // Both rails present but natural 0 (e.g. all panels empty) → no compression, 0 cells, no NaN.
    const layout = computeBodyLayout(landscape(80, rail(0), rail(0)));
    expect(layout.leftRailCells).toBe(0);
    expect(layout.rightRailCells).toBe(0);
    expect(layout.stageCells).toBe(80 - 2 /* two gaps for two present rails */);
  });

  it('handles a degenerate total of 0', () => {
    const layout = computeBodyLayout(landscape(0, rail(40), rail(30)));
    expect(layout.leftRailCells).toBe(0);
    expect(layout.rightRailCells).toBe(0);
    expect(layout.stageCells).toBe(0);
  });
});

describe('computeBodyLayout — usage INNER width derivation (L4d)', () => {
  it('LANDSCAPE: the inner width is rail − Pane chrome, NOT the raw rail width', () => {
    // A crows-driven rail of 16 once sized the gauges off the raw width and the line clipped with
    // `…` in the 12-cell inner space. The engine reports inner = 16 − 4 = 12 (no clip).
    const layout = computeBodyLayout(landscape(170, ABSENT, rail(16)));
    expect(layout.rightRailCells).toBe(16);
    expect(layout.usageInnerWidth).toBe(16 - USAGE_PANE_CHROME);
  });

  it('PORTRAIT: the inner width derives from usage SHARE of the strip WIDTH, not the strip height', () => {
    // The documented portrait mis-classification: rightRailCells is the strip HEIGHT in portrait, so
    // sizing off it was wrong. With 1 right panel the usage share is the full strip width; the strip
    // height (a few rows) is irrelevant to the gauge width.
    const layout = computeBodyLayout({
      cols: 80,
      rows: 50,
      orientation: 'portrait',
      gap: 1,
      left: ABSENT,
      right: railH(6), // a SHORT strip (6 rows) that would have read as tiny off its height
      rightPanelCount: 1,
    });
    expect(layout.axis).toBe('height');
    expect(layout.usageInnerWidth).toBe(80 - USAGE_PANE_CHROME); // full strip width, 1 panel
  });

  it('PORTRAIT: usage SPLITS the strip width with crows (2 panels), so its share shrinks', () => {
    // 2 present right panels share the strip width with one gap between → each gets ~half.
    // cols 80, gap 1, count 2 → usage share = floor((80 − 1) / 2) = 39; inner = 39 − 4 = 35.
    const wide = computeBodyLayout({
      cols: 80,
      rows: 50,
      orientation: 'portrait',
      gap: 1,
      left: ABSENT,
      right: railH(6),
      rightPanelCount: 2,
    });
    expect(wide.usageInnerWidth).toBe(Math.floor((80 - 1) / 2) - USAGE_PANE_CHROME);
    // A narrower terminal: cols 48, count 2 → share floor((48−1)/2)=23; inner 23−4=19.
    const narrow = computeBodyLayout({
      cols: 48,
      rows: 50,
      orientation: 'portrait',
      gap: 1,
      left: ABSENT,
      right: railH(6),
      rightPanelCount: 2,
    });
    expect(narrow.usageInnerWidth).toBe(Math.floor((48 - 1) / 2) - USAGE_PANE_CHROME);
  });

  it('usage-alone reserve (natural inner + chrome) seats the FULL gauge line on a wide terminal', () => {
    // railContent reserves `USAGE_NATURAL_INNER_WIDTH + USAGE_PANE_CHROME` for usage-alone; at that
    // natural width on a roomy terminal the rail seats it whole → inner = the full-line width.
    const reserve = USAGE_NATURAL_INNER_WIDTH + USAGE_PANE_CHROME;
    const layout = computeBodyLayout(landscape(300, ABSENT, rail(reserve)));
    expect(layout.rightRailCells).toBe(reserve);
    expect(layout.usageInnerWidth).toBe(USAGE_NATURAL_INNER_WIDTH);
  });

  it('no `…` clip at the COMPRESSED floor: a crows+usage rail at MIN_PANEL_WIDTH still fits a gauge', () => {
    // The tightest landscape: the right rail compresses to MIN_PANEL_WIDTH (12). Its inner width is
    // 12 − 4 = 8 = MIN_USAGE_WIDTH (the bare line `marker+space+bar(6)`), so the gauge draws without
    // a `…` clip. This is the "no clip at ANY rail width" criterion at its worst case.
    const layout = computeBodyLayout(landscape(60, rail(40), rail(40)));
    // Sanity: the right rail did compress to its min here (both naturals huge, budget tight).
    expect(layout.rightRailCells).toBeGreaterThanOrEqual(MIN_PANEL_WIDTH);
    // And the engine's reported inner width at this rail is ≥ that floor (never negative, never clips).
    expect(layout.usageInnerWidth).toBeGreaterThanOrEqual(MIN_USAGE_WIDTH);
  });
});

describe('computeBodyLayout — PORTRAIT rows-axis budget (R4 / L4b)', () => {
  it('budgets HEIGHT, not width, in portrait (naturalWidth is ignored)', () => {
    // railH carries naturalWidth=999 (huge) but a small naturalHeight — if the engine read width it
    // would compress to the budget; reading height it seats both strips at their natural height.
    const layout = computeBodyLayout(portrait(40, railH(6), railH(5)));
    expect(layout.axis).toBe('height');
    expect(layout.leftRailCells).toBe(6); // == naturalHeight, not driven by the 999 width
    expect(layout.rightRailCells).toBe(5);
  });

  it('gives each strip its natural height when the rows budget is roomy', () => {
    // 40 rows: floor=24, gaps=2 → railBudget=14; naturals 6+5=11 ≤ 14 → both natural; Stage > 60%.
    const layout = computeBodyLayout(portrait(40, railH(6), railH(5)));
    expect(layout.stageCells).toBeGreaterThanOrEqual(Math.ceil(0.6 * 40));
    expect(layout.leftRailCells + layout.rightRailCells).toBeLessThanOrEqual(14);
  });

  it('the strips + Stage + gaps never exceed the BODY height (no overdraw into the chrome — L4c)', () => {
    // The portrait counterpart of the landscape overdraw guard: in portrait `rows` IS the measured
    // Body height (App threads `useBodyLayout(bodyHeight)`), so this proves NOTHING the engine budgets
    // can spill past the Body into the chat input + footer. Sweep many Body heights × rail naturals;
    // `leftStrip + stage + rightStrip + 2 gaps` (both strips present) must always be ≤ the total.
    for (let bodyHeight = 8; bodyHeight <= 200; bodyHeight += 3) {
      for (const [lh, rh] of [
        [4, 3],
        [12, 8],
        [40, 30],
        [80, 80],
      ] as const) {
        const layout = computeBodyLayout(portrait(bodyHeight, railH(lh), railH(rh)));
        const used =
          layout.leftRailCells + layout.rightRailCells + layout.stageCells + 2; /* two gaps */
        expect(used).toBeLessThanOrEqual(bodyHeight);
      }
    }
  });

  it('never grows a strip beyond its natural height with huge slack', () => {
    const layout = computeBodyLayout(portrait(120, railH(8), railH(4)));
    expect(layout.leftRailCells).toBe(8);
    expect(layout.rightRailCells).toBe(4);
  });

  it('clamps both strips to the rows budget when their heights exceed it (well below the WIDTH-min)', () => {
    // 30 rows: floor=18, gaps=2 → railBudget=10; naturals 20+20=40 > 10 → compressed, total ≤ budget.
    // A strip can drop below the 12-cell `MIN_PANEL_WIDTH` (that is a WIDTH minimum, not a height one),
    // so the Stage floor + the other strip both survive. The height floor that DOES apply is the much
    // smaller `MIN_PORTRAIT_RAIL_HEIGHT`.
    const layout = computeBodyLayout(portrait(30, railH(20), railH(20)));
    expect(layout.leftRailCells + layout.rightRailCells).toBeLessThanOrEqual(10);
    expect(layout.leftRailCells).toBeLessThan(MIN_PANEL_WIDTH); // below the width-min — allowed
    expect(layout.rightRailCells).toBeLessThan(MIN_PANEL_WIDTH);
    expect(layout.stageCells).toBeGreaterThanOrEqual(Math.ceil(0.6 * 30)); // Stage floor still wins
  });

  it('floors each present strip at MIN_PORTRAIT_RAIL_HEIGHT when the budget seats both', () => {
    // 40 rows: floor=24, gaps=2 → railBudget=14; naturals 20+20 exceed it, but both legible-height
    // floors (6+6=12) fit → every strip keeps ≥ the floor, so the chrome-heavy Usage strip still shows
    // a couple of gauges (the "can't see the graphs in vertical mode" fix) and the Stage floor holds.
    const layout = computeBodyLayout(portrait(40, railH(20), railH(20)));
    expect(layout.leftRailCells).toBeGreaterThanOrEqual(MIN_PORTRAIT_RAIL_HEIGHT);
    expect(layout.rightRailCells).toBeGreaterThanOrEqual(MIN_PORTRAIT_RAIL_HEIGHT);
    expect(layout.stageCells).toBeGreaterThanOrEqual(Math.ceil(0.6 * 40));
  });

  it('protects the RIGHT dashboard strip first when both floors cannot fit', () => {
    // 30 rows: railBudget=10 < the two 6-row floors (12). The Stage floor is the hard invariant, so the
    // lower-priority LEFT strip yields toward 0 first and the RIGHT (usage/crows dashboard) keeps its
    // legible floor — the deliberate priority flip from the old "taller strip keeps more" split. Both
    // still stay > 0, so neither collapses entirely.
    const layout = computeBodyLayout(portrait(30, railH(20), railH(10)));
    expect(layout.leftRailCells).toBeGreaterThan(0);
    expect(layout.rightRailCells).toBeGreaterThan(0);
    expect(layout.rightRailCells).toBeGreaterThanOrEqual(layout.leftRailCells); // dashboard protected
    expect(layout.stageCells).toBeGreaterThanOrEqual(Math.ceil(0.6 * 30));
  });

  it('a collapsed strip contributes nothing and no gap in portrait', () => {
    // Only the left strip present → 1 gap; right is 0 rows.
    const layout = computeBodyLayout(portrait(50, railH(8), ABSENT));
    expect(layout.rightRailCells).toBe(0);
    expect(layout.leftRailCells).toBe(8);
    expect(layout.stageCells).toBe(50 - 8 - 1 /* one gap */);
  });

  it('portrait math is total — no NaN / negative at small heights', () => {
    for (let rows = 1; rows <= 60; rows++) {
      const layout = computeBodyLayout(portrait(rows, railH(20), railH(15)));
      for (const n of [layout.leftRailCells, layout.rightRailCells, layout.stageCells]) {
        expect(Number.isInteger(n)).toBe(true);
        expect(n).toBeGreaterThanOrEqual(0);
        expect(Number.isNaN(n)).toBe(false);
      }
      expect(layout.stageCells).toBeGreaterThanOrEqual(Math.ceil(0.6 * rows));
    }
  });

  it('zero-natural-height present strips → 0 cells, Stage takes the rest, no NaN', () => {
    const layout = computeBodyLayout(portrait(40, railH(0), railH(0)));
    expect(layout.leftRailCells).toBe(0);
    expect(layout.rightRailCells).toBe(0);
    expect(layout.stageCells).toBe(40 - 2 /* two gaps */);
  });
});

describe('computeBodyLayout — landscape ignores naturalHeight (L4b)', () => {
  it('landscape width budget is unchanged by a wildly different naturalHeight', () => {
    const a = computeBodyLayout(landscape(200, rail(24, 5), rail(18, 5)));
    const b = computeBodyLayout(landscape(200, rail(24, 999), rail(18, 999)));
    expect(a.leftRailCells).toBe(b.leftRailCells);
    expect(a.rightRailCells).toBe(b.rightRailCells);
    expect(a.stageCells).toBe(b.stageCells);
  });
});

describe('clipName / FILENAME_CAP — width contribution bound (R8)', () => {
  it('keeps the head and drops the tail past the cap', () => {
    const long = 'a-really-long-plan-filename-that-goes-on-and-on.md';
    expect(clipName(long, FILENAME_CAP)).toBe(long.slice(0, FILENAME_CAP));
    expect(clipName(long, FILENAME_CAP).length).toBe(FILENAME_CAP);
  });

  it('leaves a short name unchanged', () => {
    expect(clipName('short.md', FILENAME_CAP)).toBe('short.md');
  });

  it('is total: empty name and non-positive cap never throw', () => {
    expect(clipName('', FILENAME_CAP)).toBe('');
    expect(clipName('anything', 0)).toBe('');
    expect(clipName('anything', -5)).toBe('');
  });

  it('caps a rail width so one long name cannot inflate it (capped natural fits the slack budget)', () => {
    // A rail whose natural width was computed with the cap can never exceed cap + a small gutter, so
    // even with a pathological name it stays within the slack budget on a normal terminal.
    const cappedNatural = FILENAME_CAP + 4; // gutter (marker+space+star) added by the natural-width source
    const layout = computeBodyLayout(landscape(160, rail(cappedNatural), ABSENT));
    expect(layout.leftRailCells).toBe(cappedNatural);
    expect(layout.leftRailCells).toBeLessThanOrEqual(FILENAME_CAP + 4);
  });
});
