/**
 * View-models for the top and bottom bars (rule 2: bar presentation lives here, not in a component
 * or the store). The bars are pure functions of input state — the toggled-panel set, the effective
 * focus, and the focused panel's declared keymap — so their non-trivial formatting (the subscript
 * labels, the hint list) is a tested pure transform, not inline JSX logic.
 */

import { CHAT_FOCUS, type FocusId } from '../input/focusStore.js';
import type { Keymap } from '../input/keymap.js';
import { PANELS, type PanelId } from '../input/panels.js';

/** Unicode subscript digits 0–9, indexed by the digit — for the top bar's `plans₁ … crows₀` labels
 * (the plan's "Subscript number labels: `plans_1` … `crows_0`"). A real subscript glyph, so the
 * label reads as one token, not `plans_1`. */
const SUBSCRIPT_DIGITS = ['₀', '₁', '₂', '₃', '₄', '₅', '₆', '₇', '₈', '₉'] as const;

/** Map a `0`–`9` digit to its subscript glyph. */
function subscript(digit: number): string {
  return SUBSCRIPT_DIGITS[digit] ?? String(digit);
}

/** One top-bar label: a panel's name with its subscript digit, and whether it is currently toggled
 * on (the bar *highlights toggled panels* — the plan's "highlight currently-toggled panels"). */
export interface TopBarLabel {
  readonly id: PanelId;
  /** Display text, e.g. `plans₁`. */
  readonly text: string;
  /** True when this panel is in the visible set → the bar renders it highlighted. */
  readonly active: boolean;
}

/**
 * The top bar's labels, in screen order, each marked active iff its panel is toggled on. Pure over
 * the visible set; the component just maps these to highlighted/dim `<Text>`. Built from {@link PANELS}
 * so a new panel appears in the bar automatically (no second list to keep in sync).
 */
export function selectTopBar(visible: ReadonlySet<PanelId>): readonly TopBarLabel[] {
  return PANELS.map((panel) => ({
    id: panel.id,
    text: `${panel.id}${subscript(panel.digit)}`,
    active: visible.has(panel.id),
  }));
}

/** One contextual hint: the key and what it does, drawn straight from a declared keymap entry. */
export interface BottomBarHint {
  /** The printable chord char (`j`), or a special-key name (`enter`) for display. */
  readonly key: string;
  readonly description: string;
}

/** The always-present global hints — the chords the root dispatcher owns regardless of focus. Shown
 * first so the navigation keys are always discoverable. */
const GLOBAL_HINTS: readonly BottomBarHint[] = [
  { key: '^1–0', description: 'panels' },
  { key: '^hjkl', description: 'nav' },
  { key: '^f', description: 'chat' },
];

/** Render a chord's key for the hint bar: prefer its printable char, else name the special key. */
function hintKey(entry: Keymap<string>[number]): string {
  if (entry.chord.input !== undefined) {
    return entry.chord.input;
  }
  // A key-only chord (e.g. Enter): name the first listed special flag.
  const flags = entry.chord.key === undefined ? [] : Object.keys(entry.chord.key);
  return flags[0] ?? '?';
}

/**
 * The bottom bar's hints: the global chords, then the *focused* panel's own declared keys (the plan's
 * "Bottom bar: contextual hints", sourced from the keymap so a declared key is self-documenting —
 * see keymap.ts). When chat is focused there is no panel keymap, so only the globals show. Pure over
 * the effective focus + that panel's keymap, both passed in by the shell.
 */
export function selectBottomBar(
  focused: FocusId,
  focusedKeymap: Keymap<string> | undefined,
): readonly BottomBarHint[] {
  if (focused === CHAT_FOCUS || focusedKeymap === undefined) {
    return GLOBAL_HINTS;
  }
  const panelHints = focusedKeymap.map((entry) => ({
    key: hintKey(entry),
    description: entry.description,
  }));
  return [...GLOBAL_HINTS, ...panelHints];
}
