/**
 * View-models for the top and bottom bars (rule 2: bar presentation lives here, not in a component
 * or the store). The bars are pure functions of input state — the toggled-panel set, the effective
 * focus, and the focused panel's declared keymap — so their non-trivial formatting (the subscript
 * labels, the hint list) is a tested pure transform, not inline JSX logic.
 */

import { ACTIONS, chordLabel, type ResolvedBindings } from '../input/bindings.js';
import { CHAT_FOCUS, type FocusId } from '../input/focusStore.js';
import { GLOBAL_ACTION_IDS, GLOBAL_SCOPE, inFocusScope } from '../input/globalScope.js';
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
    text: `${panel.label ?? panel.id}${subscript(panel.digit)}`,
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

/** The navigation trio shown when a *mode* owns the bar: the chords that stay discoverable even
 * behind a modal (panels, geometric nav, focus-chat). Kept minimal on purpose — under a non-pass-
 * through mode the other globals are captured, so listing them would be a lying affordance. */
function navGlobals(bindings: ResolvedBindings): readonly BottomBarHint[] {
  const prefix = modifierPrefix(bindings);
  return [
    { key: `${prefix}1–0`, description: 'panels' },
    { key: `${prefix}hjkl`, description: 'nav' },
    { key: bindings.label('global.focusChat'), description: 'chat' },
  ];
}

/**
 * The global hints that are *usable from the current focus* — the real fix for the bar/dispatcher
 * drift. The two synthetic groups (panel digits, vim nav) lead, then every named global whose
 * {@link GLOBAL_SCOPE} entry is live from `focused`, in declaration order, labelled from the resolved
 * bindings (so a rebind / modifier choice / the murder `C-m` override all track here). `global.keyHelp`
 * is emitted separately as the right-pinned help hint, so it is skipped in the loop.
 *
 * Nav is itself focus-aware: away from chat all of `hjkl` move focus, but IN chat `A-h`/`A-l` are
 * stolen by the chat-target cycle super-chords (see dispatcher.ts), so only `A-j`/`A-k` still
 * navigate — the hint shows the truthful subset rather than claiming four working arrows.
 */
function globalHints(bindings: ResolvedBindings, focused: FocusId): readonly BottomBarHint[] {
  const prefix = modifierPrefix(bindings);
  const hints: BottomBarHint[] = [{ key: `${prefix}1–0`, description: 'panels' }];
  hints.push(
    focused === CHAT_FOCUS
      ? { key: `${prefix}jk`, description: 'nav' }
      : { key: `${prefix}hjkl`, description: 'nav' },
  );
  for (const id of GLOBAL_ACTION_IDS) {
    if (id === 'global.keyHelp') {
      continue; // rendered as the right-pinned help hint, with the chat-focus `?`-types disambiguation
    }
    if (!inFocusScope(GLOBAL_SCOPE[id], focused)) {
      continue;
    }
    // The two chat-target cycle chords are mirror directions of one gesture; in chat focus they
    // collapse into a single `target` hint (`A-hl`/`C-hl`, matching the nav `jk` style) to save
    // horizontal space rather than spending two slots on `prev target` + `next target`.
    if (id === 'global.cycleTargetNext') {
      continue; // folded into the combined `target` hint emitted at cycleTargetPrev's position
    }
    if (id === 'global.cycleTargetPrev') {
      hints.push({ key: `${prefix}hl`, description: 'target' });
      continue;
    }
    hints.push({ key: bindings.label(id), description: ACTIONS[id].description });
  }
  return hints;
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
  if (modeHints !== undefined) {
    // A mode owns the bar: the nav trio (still discoverable) then the mode's own hints; no panel keys,
    // and no help hint (a modal's keys are the only relevant ones). The other globals are captured by
    // a non-pass-through mode, so the bar lists only the always-discoverable navigation chords.
    return [...navGlobals(bindings), ...modeHints];
  }
  // The globals usable from THIS focus (the dispatcher's gate, shared via GLOBAL_SCOPE), so a live
  // chord is always hinted and a dead one never is.
  const globals = globalHints(bindings, focused);
  // Item 12: the keybinding-help hint, ALWAYS pinned to the far right so a new user can find it. The
  // label is derived from the resolved `global.keyHelp` binding (so a rebind tracks here too).
  //
  // While CHAT has focus, a bare `?` types into the input (the dispatcher deliberately never steals
  // it — dispatcher.ts gates `global.keyHelp` to non-chat focus), so a plain `?` hint would be a lying
  // affordance. The reachable affordance from the input is the `:help` command (commandDispatch.ts),
  // which is self-describing — so the chat-focus help hint is just `:help`, with no redundant trailing
  // word. Away from chat the bare `?` is live, shown as `? help`.
  const helpHint: BottomBarHint =
    focused === CHAT_FOCUS
      ? { key: ':help', description: '', align: 'right' }
      : { key: bindings.label('global.keyHelp'), description: 'help', align: 'right' };
  if (focused === CHAT_FOCUS || focusedKeymap === undefined) {
    return [...globals, helpHint];
  }
  // `hidden` entries (mechanical sub-steps of a gesture, e.g. go-to-line digits) stay matchable but
  // are not hints — see keymap.ts.
  const panelHints = focusedKeymap
    .filter((entry) => entry.hidden !== true)
    .map((entry) => ({
      key: hintKey(entry),
      description: entry.description,
    }));
  return [...globals, ...panelHints, helpHint];
}
