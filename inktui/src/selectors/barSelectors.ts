/**
 * View-models for the top and bottom bars (rule 2: bar presentation lives here, not in a component
 * or the store). The bars are pure functions of input state — the toggled-panel set, the effective
 * focus, and the focused panel's declared keymap — so their non-trivial formatting (the subscript
 * labels, the hint list) is a tested pure transform, not inline JSX logic.
 */

import type { ResolvedBindings } from '../input/bindings.js';
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
}

/** The modifier prefix for the digit/nav hints, derived from the resolved bindings so the footer
 * tracks the user's modifier choice. Reads `global.focusChat`'s label (always present) and keeps just
 * its prefix (`M-`, `C-`, or `M-/C-` under both). */
function modifierPrefix(bindings: ResolvedBindings): string {
  // The label is e.g. `M-space`; strip the key part to get the prefix(es). Under `both` it is
  // `M-space/C-space` → `M-/C-`.
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

/** Render a chord's key for the hint bar: prefer its printable char, else name the special key. */
function hintKey(entry: Keymap<string>[number]): string {
  const chord = firstChord(entry.chord);
  if (chord.input !== undefined) {
    return chord.input;
  }
  // A key-only chord (e.g. Enter): name the first listed special flag.
  const flags = chord.key === undefined ? [] : Object.keys(chord.key);
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
  bindings: ResolvedBindings,
): readonly BottomBarHint[] {
  const globals = globalHints(bindings);
  if (focused === CHAT_FOCUS || focusedKeymap === undefined) {
    return globals;
  }
  const panelHints = focusedKeymap.map((entry) => ({
    key: hintKey(entry),
    description: entry.description,
  }));
  return [...globals, ...panelHints];
}
