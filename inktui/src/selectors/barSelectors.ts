/**
 * View-models for the top and bottom bars (rule 2: bar presentation lives here, not in a component
 * or the store). The bars are pure functions of input state — the toggled-panel set, the effective
 * focus, and the focused panel's declared keymap — so their non-trivial formatting (the subscript
 * labels, the hint list) is a tested pure transform, not inline JSX logic.
 */

import { chordLabel, type ResolvedBindings } from '../input/bindings.js';
import { CHAT_FOCUS, type FocusId } from '../input/focusStore.js';
import type { KeyChord, Keymap } from '../input/keymap.js';
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
  /** When `'right'`, the bar pins this hint to the FAR right of the bar (item 12 prep — the help
   * hint a new user can always find). Omitted/`'left'` = normal left-to-right flow. */
  readonly align?: 'left' | 'right';
}

/** The modifier prefix for the digit/nav hints, derived from the resolved bindings so the footer
 * tracks the user's modifier choice. Reads `global.focusChat`'s label (always present) and keeps just
 * its prefix (`A-`, `C-`, or `A-/C-` under both). */
function modifierPrefix(bindings: ResolvedBindings): string {
  // The label is e.g. `A-space`; strip the key part to get the prefix(es). Under `both` it is
  // `A-space/C-space` → `A-/C-`.
  return bindings
    .label('global.focusChat')
    .split('/')
    .map((part) => part.replace(/space$/, ''))
    .join('/');
}

/** The always-present global hints — the chords the root dispatcher owns regardless of focus. Shown
 * first so the navigation keys are always discoverable. Built from the resolved bindings so the
 * labels track the modifier + any rebinds. */
function globalHints(bindings: ResolvedBindings): readonly BottomBarHint[] {
  const prefix = modifierPrefix(bindings);
  return [
    { key: `${prefix}1–0`, description: 'panels' },
    { key: `${prefix}hjkl`, description: 'nav' },
    { key: bindings.label('global.focusChat'), description: 'chat' },
  ];
}

/** Normalize a keymap entry's chord(s) to the first chord (the list form binds equivalent chords;
 * the hint shows one). A list always has at least one chord (resolved bindings never empty). */
function firstChord(chord: KeyChord | readonly KeyChord[]): KeyChord {
  if (Array.isArray(chord)) {
    return (chord as readonly KeyChord[])[0] as KeyChord;
  }
  return chord as KeyChord;
}

/**
 * Render a chord's key for the hint bar via the shared {@link chordLabel} — so a command-modified
 * panel key (e.g. star = alt+f) shows its modifier prefix (`A-f` / `C-f`, varying with the configured
 * modifier) instead of a bare, un-pressable `f`, while a plain key (`j`, Enter) reads as itself. One
 * label rule for the panel hints and the globals, so the focused pane's keys never display a modifier
 * the bar's nav/chat hints don't.
 */
function hintKey(entry: Keymap<string>[number]): string {
  return chordLabel(firstChord(entry.chord));
}

/**
 * The bottom bar's hints: the global chords, then the *focused* panel's own declared keys (the plan's
 * "Bottom bar: contextual hints", sourced from the keymap so a declared key is self-documenting —
 * see keymap.ts). When chat is focused there is no panel keymap, so only the globals show.
 *
 * When an active mode supplies its own `modeHints` (the spawn wizard, the help overlay, etc.), THOSE
 * replace the panel keys entirely — the mode captures input, so its keys are the only relevant ones
 * (the panels underneath can't be driven). The globals still lead so the navigation keys stay
 * discoverable. Pure over the effective focus, that panel's keymap, and the active mode's hints,
 * all passed in by the shell.
 */
export function selectBottomBar(
  focused: FocusId,
  focusedKeymap: Keymap<string> | undefined,
  bindings: ResolvedBindings,
  modeHints?: readonly BottomBarHint[],
): readonly BottomBarHint[] {
  const globals = globalHints(bindings);
  if (modeHints !== undefined) {
    // A mode owns the bar: globals (still discoverable) then the mode's own hints; no panel keys, and
    // no help hint (a modal's keys are the only relevant ones).
    return [...globals, ...modeHints];
  }
  // Item 12: the keybinding-help hint, ALWAYS pinned to the far right so a new user can find it. The
  // label is derived from the resolved `global.keyHelp` binding (so a rebind tracks here too).
  const helpHint: BottomBarHint = {
    key: bindings.label('global.keyHelp'),
    description: 'help',
    align: 'right',
  };
  if (focused === CHAT_FOCUS || focusedKeymap === undefined) {
    return [...globals, helpHint];
  }
  const panelHints = focusedKeymap.map((entry) => ({
    key: hintKey(entry),
    description: entry.description,
  }));
  return [...globals, ...panelHints, helpHint];
}
