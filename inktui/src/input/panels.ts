/**
 * Panel identity and the **number → screen-position** mapping (the plan's guiding principle:
 * "Numbers map to screen position").
 *
 * View state in this app is *not* a `_view` enum (the old stringly-typed anti-pattern) — it is the
 * set of panels currently toggled on (see {@link ./panelStore.js}). This file owns the closed set
 * of panel ids and the total, explicit map from a `ctrl+<n>` digit to the panel it toggles. Keeping
 * the digit→panel relation here (one table, total over the digits we use) means the dispatcher and
 * the top bar both read the same source of truth, and adding a panel is one table entry, not a
 * switch scattered across the dispatcher.
 *
 * Screen positions (from the plan's Layout section):
 *   1 plans · 2 notes · 3 reports · 4 tickets · 5 history   → the left region
 *   8 tree · 9 usage · 0 crows                              → the right region
 *   6–7 reserved — deliberately absent so an unbound digit is a no-op.
 */

/** The closed set of panel ids. A string union keyed by domain, not by number, so code reads
 * intent (`'plans'`) while the *binding* to a number stays in {@link DIGIT_TO_PANEL}. */
export type PanelId =
  | 'plans'
  | 'notes'
  | 'reports'
  | 'tickets'
  | 'history'
  | 'usage'
  | 'tree'
  | 'crows';

/** Which screen region a panel renders in. The focus geometry and the shell layout both need this;
 * derived from the number (1–4 left, 9/0 right) but named so call sites don't re-derive it. */
export type PanelRegion = 'left' | 'right';

/** One panel's static placement: the digit that toggles it and the region it lives in. */
export interface PanelPlacement {
  readonly id: PanelId;
  readonly digit: PanelDigit;
  readonly region: PanelRegion;
  /** The user-facing label for the top bar, when it must differ from the internal {@link id}.
   * Omitted = the id doubles as the label. */
  readonly label?: string;
}

/** The digits that bind to a panel. A literal union (not `number`) so {@link DIGIT_TO_PANEL} is
 * checked total over exactly these and a stray digit can't silently map to nothing at a type
 * level. `6`–`7` are intentionally excluded (reserved); `8` binds `tree`. */
export type PanelDigit = 1 | 2 | 3 | 4 | 5 | 8 | 9 | 0;

/**
 * The single source of truth for panel placement, in screen order (left region first, then right).
 * Declaration order here is the order the focus ring and top bar present panels, and the final
 * tiebreak the geometry kernel uses — so it is deliberately screen order, not alphabetical.
 */
export const PANELS: readonly PanelPlacement[] = [
  { id: 'plans', digit: 1, region: 'left' },
  { id: 'notes', digit: 2, region: 'left' },
  { id: 'reports', digit: 3, region: 'left' },
  { id: 'tickets', digit: 4, region: 'left' },
  { id: 'history', digit: 5, region: 'left' },
  { id: 'tree', digit: 8, region: 'right' },
  { id: 'usage', digit: 9, region: 'right' },
  { id: 'crows', digit: 0, region: 'right' },
];

/** Every panel id, in screen order — the canonical iteration order for the visible-set and ring. */
export const PANEL_IDS: readonly PanelId[] = PANELS.map((p) => p.id);

/**
 * Total map from a `ctrl+<n>` digit to the panel it toggles. Built from {@link PANELS} so the two
 * never drift. A digit not present (6–7, or any non-digit) is simply absent → the dispatcher treats
 * `ctrl+<that>` as a no-op, which is the correct "reserved/unbound" behaviour.
 */
export const DIGIT_TO_PANEL: Readonly<Record<PanelDigit, PanelId>> = Object.fromEntries(
  PANELS.map((p) => [p.digit, p.id]),
) as Record<PanelDigit, PanelId>;

/** Look up the panel a typed digit string (`'1'`, `'0'`, …) toggles, or `null` if the digit is
 * unbound/reserved. The dispatcher receives the raw input char from Ink, so this takes a string and
 * does the narrowing in one place. */
export function panelForDigit(input: string): PanelId | null {
  // Must be exactly one ASCII digit `0`–`9`. (Guard against `Number(' ')`/`Number('')` === 0, which
  // would otherwise map whitespace/empty input to digit 0's panel — e.g. alt+space hitting crows.)
  if (input.length !== 1 || input < '0' || input > '9') {
    return null;
  }
  const n = Number(input);
  if (!Number.isInteger(n)) {
    return null;
  }
  // `n` is 0–9 here; index the partial record and let the `undefined` (reserved digits) fall to
  // null. `as PanelDigit` is the index key; the lookup itself is what decides bound vs reserved.
  return DIGIT_TO_PANEL[n as PanelDigit] ?? null;
}
