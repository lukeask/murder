/**
 * `budget.ts` — the pure layout-budget engine (R1–R7). No React, no store, no Ink imports.
 *
 * The whole responsive-layout refactor turns on ONE question: how many cells (columns in landscape,
 * rows in portrait) does each side rail get, so the center Stage keeps its guaranteed ≥60% share?
 * Everything downstream falls out of that number: the Ledger already drops trailing columns as its
 * measured width shrinks (`columnsForWidth`, `WIDTH_PER_COLUMN`), so sizing a rail tighter makes its
 * rightmost columns collapse for free (R3). This module is therefore the testable keystone — pure
 * data → numbers — and it is the layer the flex-blind test suite (ink-testing-library runs no Yoga)
 * can actually guard. Flex/layout correctness is proven only in live tmux verification (L7), NEVER by
 * these unit tests.
 *
 * ## The sanctioned absolutes (R7)
 * The user's directive: "The only absolutes allowed are the absolute minimum to be shown in the
 * smallest possible configured form." The four constants below are exactly those — the smallest
 * legible width of a panel/gauge and the hard Stage floor. Every other size is derived relatively
 * from the live terminal `cols`/`rows` (R5). They live HERE, in one place, marked as the sanctioned
 * absolutes, so an audit finds them all at once.
 *
 * ## Invariant ordering (the one thing that must not be gotten wrong)
 * The Stage floor and `MIN_PANEL_WIDTH` conflict at small terminals: clamping both rails to their
 * minimums can push the Stage below 60%. The directive resolves it — "the central Stage must always
 * get at least 60%" — so the Stage floor is the HARD invariant and `MIN_PANEL_WIDTH` is best-effort.
 * When the rail budget cannot seat even the minimums, the lower-priority LEFT rail yields toward 0
 * first (the right rail is already minimal per R6), so the Stage floor is never breached.
 *
 * ## Two axes, two rail policies (L4b)
 * Landscape budgets WIDTH with the `MIN_PANEL_WIDTH` compression above; portrait budgets the ROWS
 * axis with NO width-minimum (it is a width concept) — a rail STRIP is simply as tall as its tallest
 * panel needs, capped to its share of the rows budget. So the two orientations have distinct
 * branches; only the Stage's ≥60% floor + the gap accounting are shared. See `computeBodyLayout`.
 */

/**
 * Marker + star gutter + a few name chars — below this a left panel can no longer legibly draw a row.
 * Smallest-legible-form absolute (R7).
 */
export const MIN_PANEL_WIDTH = 12;

/**
 * The mini usage-gauge floor — the narrowest INNER width a usage gauge can still be read at: the mini
 * line `marker(1) + space(1) + MINI_BAR_WIDTH(6)` = 8 (see {@link ../components/UsagePanel.tsx}). It is
 * exactly `MIN_PANEL_WIDTH − USAGE_PANE_CHROME`, so a usage gauge always fits inside even the smallest
 * legible right rail without a `…` clip (L4d). Smallest-legible-form absolute (R7).
 */
export const MIN_USAGE_WIDTH = 8;

/** The Stage's hard floor as a fraction of the axis total (R3 landscape / R4 portrait). */
export const STAGE_MIN_FRACTION = 0.6;

/**
 * Head-clip cap for a filename's width CONTRIBUTION (R8). One pathologically long name must not
 * inflate a rail's natural width, so the natural-width computation caps the name length here (keep
 * the head, drop the tail — see {@link clipName}). Render-time truncation (`wrap="truncate"`) already
 * keeps the head; this caps how wide the rail is allowed to grow for it. Tune in live-verify (L7).
 */
export const FILENAME_CAP = 28;

/** The three usage-gauge size variants (R9), chosen by the width the right rail allots the gauge. */
export type UsageTier = 'mini' | 'medium' | 'large';

/**
 * The Pane chrome a gauge renders INSIDE: border (1 each side) + `paddingX={1}` (1 each side) = 4
 * cells the rail width loses before any gauge glyph is drawn (see {@link ../components/Pane.tsx}). NOT
 * a free absolute (R7) — it is DERIVED from the Pane's fixed border+padding, so the tier is chosen from
 * the INNER width the gauges actually get (`rail − USAGE_PANE_CHROME`), not the raw rail width. (L4d:
 * the original bug was classifying off the raw rail width, so a crows-driven rail of ~16 picked
 * `medium` and the 20-cell medium line clipped with `…` in the 12-cell inner space.)
 */
export const USAGE_PANE_CHROME = 4;

/**
 * Usage-tier INNER-width thresholds — the exact width each tier's widest line RENDERS at (derived from
 * the {@link ../components/UsagePanel.tsx} gauge/key-line layout, NOT free absolutes — R7). A gauge
 * line is `marker(1) + space(1) + bar + …labels`; a tier "fits" when the INNER width (rail minus
 * {@link USAGE_PANE_CHROME}) is at least its rendered width, so nothing is ever truncated to `…`:
 *  - `large`  = 33: `marker(1)+sp(1)+bar(12)+gap(2)+pct(4)+gap(2)+win(3)+sp(1)+reset(7)` = 33.
 *  - `medium` = 20: `marker(1)+sp(1)+bar(12)+gap(2)+pct(4)` = 20 (the window/reset trail dropped).
 *  - `mini`   = the compact bar only: `marker(1)+sp(1)+bar(MINI_BAR_WIDTH)` — floored at
 *    {@link MIN_USAGE_WIDTH}; sized so it fits even the smallest right rail's inner width
 *    (`MIN_PANEL_WIDTH − USAGE_PANE_CHROME` = 8), so a compressed crows+usage rail never clips it.
 * The actual mini/medium/large RENDERING is L4; this module classifies the inner width so the engine
 * can report which tier the gauges can draw without a `…` clip (the L4d "no clip at any rail width").
 */
export const USAGE_TIER_LARGE_MIN = 33;
export const USAGE_TIER_MEDIUM_MIN = 20;

/** The natural cross-axis sizes + presence of one side's rail content (R2/R4/R6). */
export interface RailContent {
  /**
   * LANDSCAPE natural cross-axis size = widest row (incl. title row) across this rail's visible
   * panels, names already capped. The size the rail wants when it sits BESIDE the Stage.
   */
  readonly naturalWidth: number;
  /**
   * PORTRAIT natural cross-axis size = the tallest of this rail's visible panels' content HEIGHTS in
   * lines (title/top-border + header + rows×linesPerEntry + bottom border). In portrait the rail is a
   * horizontal strip above/below the Stage, so its needed HEIGHT is the tallest panel in the strip
   * (panels sit side-by-side, each as tall as it wants). Used only in the portrait rows-axis budget
   * (R4); ignored entirely in landscape. (L4b.)
   */
  readonly naturalHeight: number;
  /** True if this rail has any visible panel (else the rail collapses and contributes 0 + no gap). */
  readonly present: boolean;
}

/** The live inputs the body layout is budgeted from — all relative to the terminal `cols`/`rows`. */
export interface BodyLayoutInput {
  readonly cols: number;
  readonly rows: number;
  readonly orientation: 'landscape' | 'portrait';
  /** Inter-region gap in cells (App renders `columnGap`/`rowGap` = 1 between each region). */
  readonly gap: number;
  readonly left: RailContent;
  /** `right.naturalWidth` = the crow-ledger width when crows are on (R6); usage adapts to the rail. */
  readonly right: RailContent;
  /**
   * Count of PRESENT right-rail panels (usage and/or crows). In LANDSCAPE the usage gauge stacks at
   * the full rail width below crows, so its inner width is `rightRailCells − chrome`. In PORTRAIT the
   * right strip lays its panels out SIDE-BY-SIDE (each `flexGrow={1}`), so usage gets only its share of
   * the strip's WIDTH: `floor((cols − gaps) / rightPanelCount)`. The engine needs the count to derive
   * that share and pick the usage tier from the gauges' ACTUAL inner width per orientation (L4d, fixes
   * the documented portrait mis-classification). Defaults to 1 when omitted (a single right panel).
   */
  readonly rightPanelCount?: number;
}

/** The computed cell budget for the body's three regions (R1–R4) plus the usage tier (R9). */
export interface BodyLayout {
  /** Explicit cross-axis cells for the left rail (width in landscape, height share in portrait); 0 if absent. */
  readonly leftRailCells: number;
  /** Explicit cross-axis cells for the right rail; 0 if absent. */
  readonly rightRailCells: number;
  /** Cells the Stage is guaranteed — always `≥ ceil(STAGE_MIN_FRACTION * total)` by construction. */
  readonly stageCells: number;
  /** Largest usage tier whose rendered width ≤ the gauges' INNER width (R9; L4d — inner, not rail). */
  readonly usageTier: UsageTier;
  /**
   * The INNER width (cells) the usage gauges actually get to draw in, after the Pane chrome and (in
   * portrait) the side-by-side split with crows. The {@link usageTier} is `usageTierFor(usageInnerWidth)`.
   * Exposed so a test (and live-verify) can check the derivation directly.
   */
  readonly usageInnerWidth: number;
  /** Which dimension was budgeted: landscape → width, portrait → height. */
  readonly axis: 'width' | 'height';
}

/**
 * Head-clip a name to `cap` (R8): keep the FIRST `cap` characters, drop the tail. Used here to bound
 * a name's width CONTRIBUTION so one long filename cannot inflate a rail; the render layer reuses it
 * (later pass) to display the kept head. A name already within `cap` is returned unchanged; a
 * non-positive `cap` yields the empty string (degenerate, but total — never throws).
 */
export function clipName(name: string, cap: number): string {
  if (cap <= 0) {
    return '';
  }
  return name.length <= cap ? name : name.slice(0, cap);
}

/**
 * Classify the gauges' INNER width into the largest usage tier that renders without a `…` clip (R9,
 * L4d). The argument is the width the gauges actually draw in (rail minus the Pane chrome, and in
 * portrait minus the side-by-side split with crows) — NOT the raw rail width. Pure; exported so the
 * tier boundary is unit-testable independently of the full body layout.
 */
export function usageTierFor(usageInnerWidth: number): UsageTier {
  if (usageInnerWidth >= USAGE_TIER_LARGE_MIN) {
    return 'large';
  }
  if (usageInnerWidth >= USAGE_TIER_MEDIUM_MIN) {
    return 'medium';
  }
  return 'mini';
}

/**
 * The INNER width (cells) the usage gauges get to draw in, per orientation (L4d). The Pane always eats
 * {@link USAGE_PANE_CHROME}; portrait additionally splits the strip's WIDTH across the present right
 * panels (they sit side-by-side, each `flexGrow={1}`), so usage gets `floor((cols − gaps) / count)`.
 *  - LANDSCAPE: usage stacks full-rail-width below crows → inner = `rightRailCells − chrome`.
 *  - PORTRAIT:  usage shares the strip width with crows → inner = `floor((cols − gaps)/count) − chrome`.
 * Floored at 0 (totality — never negative, even when the rail is narrower than the chrome). Pure.
 */
function usageInnerWidthFor(
  landscape: boolean,
  rightRailCells: number,
  cols: number,
  gap: number,
  rightPanelCount: number,
): number {
  if (landscape) {
    return Math.max(0, rightRailCells - USAGE_PANE_CHROME);
  }
  // Portrait: the right strip spans the terminal width; its panels split it side-by-side with one gap
  // between each. usage's share is the strip width divided by the present right-panel count.
  const count = Math.max(1, Math.floor(rightPanelCount));
  const interGaps = Math.max(0, Math.floor(gap)) * (count - 1);
  const usageShare = Math.floor(Math.max(0, cols - interGaps) / count);
  return Math.max(0, usageShare - USAGE_PANE_CHROME);
}

/**
 * Compute the body's cell budget for the current terminal size + orientation + rail contents.
 *
 * Same algorithm on both axes; `total` = cols in landscape, rows in portrait (R3/R4 share the math).
 * The Stage floor is the HARD invariant (see the module header); the rails take only what's left, as
 * close to their natural sizes as the budget allows, and the lower-priority LEFT rail yields first
 * when even the minimums don't fit. Every result is floored to a non-negative integer (totality —
 * no NaN/negative cells at any size down to the smallest legible form).
 *
 * The two orientations budget DIFFERENT axes and DIFFERENT rail policies (L4b):
 *  - LANDSCAPE budgets the WIDTH axis with the `MIN_PANEL_WIDTH` compression — a rail must stay at
 *    least one legible column wide, and trailing Ledger columns drop as it compresses (R3).
 *  - PORTRAIT budgets the ROWS axis where there is NO width-minimum to honour: a rail strip's content
 *    HEIGHT is whatever its tallest panel needs, and a strip simply takes `min(naturalHeight, share
 *    of the rail budget)` (R4). Routing height through `MIN_PANEL_WIDTH` would impose a spurious
 *    12-ROW floor that, at small heights, would force one strip to 0 — so portrait gets its own
 *    branch (see {@link computePortraitRails}).
 *
 * Shared steps (both axes):
 *  1. `stageFloor = ceil(STAGE_MIN_FRACTION * total)` — the Stage's guaranteed minimum.
 *  2. `gaps = gap * (number of PRESENT rails)` — App draws one gap between each present rail and Stage.
 *  3. `railBudget = max(0, total - stageFloor - gaps)` — the most the rails may collectively take.
 *  4. The rails take `≤ railBudget`, then `stageCells = total - left - right - gaps` (≥ stageFloor).
 *  5. `usageTier` = the largest tier that fits the right rail (R9; meaningful in landscape — width).
 */
export function computeBodyLayout(input: BodyLayoutInput): BodyLayout {
  const { cols, rows, orientation, gap, left, right, rightPanelCount = 1 } = input;
  const landscape = orientation === 'landscape';
  const axis: 'width' | 'height' = landscape ? 'width' : 'height';
  // The axis we budget: columns in landscape (rails are side-by-side), rows in portrait (rails stack).
  const total = Math.max(0, Math.floor(landscape ? cols : rows));

  // A non-present rail contributes neither size nor a gap (it collapses out of the layout). The
  // natural size read is WIDTH in landscape (rails beside the Stage) and HEIGHT in portrait (rails
  // stacked above/below it) — the one orientation-specific input besides `total`.
  const leftPresent = left.present;
  const rightPresent = right.present;
  const naturalOf = (c: RailContent): number =>
    Math.max(0, Math.floor(landscape ? c.naturalWidth : c.naturalHeight));
  const leftNatural = leftPresent ? naturalOf(left) : 0;
  const rightNatural = rightPresent ? naturalOf(right) : 0;

  // 1. Stage floor — the HARD invariant. 2. One gap per present rail. 3. The rail budget is whatever
  //    is left after reserving the floor and the gaps (never negative).
  const stageFloor = Math.ceil(STAGE_MIN_FRACTION * total);
  const presentRailCount = (leftPresent ? 1 : 0) + (rightPresent ? 1 : 0);
  const gaps = Math.max(0, Math.floor(gap)) * presentRailCount;
  const railBudget = Math.max(0, total - stageFloor - gaps);

  const { leftCells, rightCells } = landscape
    ? compressLandscapeRails(leftNatural, rightNatural, leftPresent, rightPresent, railBudget)
    : computePortraitRails(leftNatural, rightNatural, railBudget);

  // The Stage takes everything the rails and gaps leave. ≥ stageFloor by construction (the rails
  // never collectively exceed `railBudget = total - stageFloor - gaps`), but `max` belt-and-braces
  // against any rounding so the contract "stageCells ≥ stageFloor" holds exactly.
  const stageCells = Math.max(stageFloor, total - leftCells - rightCells - gaps);

  // The usage tier is chosen from the gauges' ACTUAL inner width (rail − Pane chrome; in portrait also
  // the side-by-side split with crows), NOT the raw rail cells — so the chosen tier always renders
  // without a `…` clip and portrait is no longer mis-classified off the strip HEIGHT (L4d).
  const usageInnerWidth = usageInnerWidthFor(landscape, rightCells, cols, gap, rightPanelCount);

  return {
    leftRailCells: leftCells,
    rightRailCells: rightCells,
    stageCells,
    usageTier: usageTierFor(usageInnerWidth),
    usageInnerWidth,
    axis,
  };
}

/**
 * LANDSCAPE rail widths (R1–R3). Slack case: both rails fit at their natural widths, so the Stage
 * gets the (>60%) rest. Compression case: scale both rails down toward `railBudget`, proportional to
 * natural size but never below `MIN_PANEL_WIDTH`; if even the minimums don't fit, the lower-priority
 * LEFT rail yields toward 0 first (the right rail is already minimal per R6) so the Stage's 60% floor
 * is never breached. The narrower rail feeds straight into `columnsForWidth`, dropping trailing
 * columns (R3). Pure — every result is a non-negative integer.
 */
function compressLandscapeRails(
  leftNatural: number,
  rightNatural: number,
  leftPresent: boolean,
  rightPresent: boolean,
  railBudget: number,
): { leftCells: number; rightCells: number } {
  // Slack case: the rails fit at their natural sizes.
  const desired = leftNatural + rightNatural;
  if (desired <= railBudget) {
    return { leftCells: leftNatural, rightCells: rightNatural };
  }
  // Compression. First try to honour `MIN_PANEL_WIDTH` on every present rail: the minimum a rail
  // needs to stay legible. If both minimums fit the budget, distribute proportionally to natural
  // size but never below the minimum. If the minimums together OVERFLOW the budget, the Stage floor
  // wins (R3): the lower-priority LEFT rail yields toward 0 first.
  const leftMin = leftPresent ? Math.min(MIN_PANEL_WIDTH, leftNatural) : 0;
  const rightMin = rightPresent ? Math.min(MIN_PANEL_WIDTH, rightNatural) : 0;
  if (leftMin + rightMin > railBudget) {
    // Even the minimums don't fit — protect the Stage by yielding the left rail first, then the
    // right. Each clamps to what remains so neither breaches the floor (both can reach 0).
    const rightCells = Math.min(rightMin, railBudget);
    const leftCells = Math.max(0, Math.min(leftMin, railBudget - rightCells));
    return { leftCells, rightCells };
  }
  // The minimums fit; share the SLACK above the minimums proportional to each rail's natural size.
  // `desired > 0` here (else the slack branch would have taken it), so the proportional divide is
  // safe. Never grow a rail past its natural size (slack is for compression headroom, not padding).
  const slack = railBudget - (leftMin + rightMin);
  const leftSlack = Math.floor((slack * leftNatural) / desired);
  const rightSlack = Math.floor((slack * rightNatural) / desired);
  return {
    leftCells: Math.min(leftNatural, leftMin + leftSlack),
    rightCells: Math.min(rightNatural, rightMin + rightSlack),
  };
}

/**
 * PORTRAIT rail-strip heights (R4, L4b). Each present strip takes `min(naturalHeight, its share of
 * the rail budget)`, where the budget is what remains after the Stage's ≥60%-rows floor and the gaps.
 * There is NO `MIN_PANEL_WIDTH` (it is a WIDTH minimum and would impose a spurious row floor) — a
 * strip is simply as tall as its tallest panel, capped to its budget share. Slack case (both fit):
 * each gets its natural height. Tight case: the budget is split proportional to natural height (so a
 * taller strip keeps more rows), each still capped to its natural height. The Stage absorbs the rest,
 * so it always keeps ≥60% of rows. Pure — non-negative integers, no NaN even when budget or naturals
 * are 0.
 */
function computePortraitRails(
  leftNatural: number,
  rightNatural: number,
  railBudget: number,
): { leftCells: number; rightCells: number } {
  // Slack case: both strips fit at their natural heights, the Stage takes the (>60%) rest.
  const desired = leftNatural + rightNatural;
  if (desired <= railBudget) {
    return { leftCells: leftNatural, rightCells: rightNatural };
  }
  // Tight case: split the budget proportional to natural height, capped to each strip's natural
  // height. `desired > 0` here (the slack branch handles `desired === 0`), so the divide is safe.
  const leftCells = Math.min(leftNatural, Math.floor((railBudget * leftNatural) / desired));
  const rightCells = Math.min(rightNatural, Math.floor((railBudget * rightNatural) / desired));
  return { leftCells, rightCells };
}
