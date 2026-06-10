/**
 * The central binding registry — the one place that knows *which key* fires *which action* and how
 * the user's command-modifier choice (alt / ctrl / both) maps onto raw Ink `(input, key)` events.
 *
 * ## Why this exists
 *
 * The Ink rewrite scattered command-chord literals across the dispatcher, the panels, and the hint
 * bar (`{ input: 's', key: { meta: true } }` repeated everywhere). Every new feature lands a keybind,
 * and the settings menu (later phase) lets the user pick the modifier and rebind a few keys — so a
 * literal-per-call-site model does not scale. This module is a **deep module** in the Ousterhout
 * sense: callers name an {@link ActionId} and ask "what chord(s) is this?" / "does this event match
 * it?" / "what label do I show?"; they never inspect the modifier or know whether it is alt or ctrl.
 * All modifier logic — including the alt↔ctrl degradation when ctrl is unavailable, and the
 * `both` two-chord expansion — is hidden behind {@link resolveBindings}.
 *
 * ## Pure, no React
 *
 * This is plain data + pure functions. The React/store wiring lives in
 * {@link ./bindingsStore.js bindingsStore} (a vanilla Zustand store) and `useBindings()`. A test can
 * resolve bindings and assert matching with no rendering.
 *
 * ## Today's behavior is the default
 *
 * `resolveBindings('alt', false, {})` reproduces the current TUI exactly: command actions are
 * `alt+<key>`, the command modifier is `meta` (Ink reports Alt as `key.meta`). Phase 1 changes no
 * behavior — it just routes the existing literals through here.
 */

import type { Key } from 'ink';
import type { KeyChord } from './keymap.js';

/** The command modifier the user has chosen. `both` accepts either alt or ctrl for command actions
 * (e.g. while the user is migrating muscle memory). `ctrl` requires the kitty protocol (a later
 * phase); when unavailable it degrades to `alt` — see {@link resolveBindings}. */
export type Modifier = 'alt' | 'ctrl' | 'both';

/**
 * The closed set of named actions. Grows one entry per feature. Two namespaces:
 *  - `global.*` — app-wide chords the root dispatcher owns (the global-chord layer), live regardless
 *    of focus.
 *  - `panel.*` — chords a focused panel binds in its own keymap (e.g. favorite/star the cursor row).
 *
 * Note: panel-digit toggles (`alt+1`–`0`) and vim directional nav (`alt+h/j/k/l`) are command-
 * modified but are NOT individual actions — they are total tables in {@link ./panels.js} / the
 * dispatcher's `VIM_NAV`, gated by {@link ResolvedBindings.isCommandModified}. Only single-purpose
 * named chords live here.
 */
export type ActionId =
  | 'global.focusChat' // alt+space — focus the chat input
  | 'global.spawn' // alt+s — open the spawn wizard (chat-focus scoped, see dispatcher)
  | 'global.tmux' // alt+y — toggle the tmux/parsed fullscreen view
  | 'global.newPlan' // alt+p — open the new-plan popup
  | 'global.newTicket' // alt+t — open the new-ticket popup
  | 'global.settings' // alt+o / ctrl+o — open the settings modal
  | 'panel.star'; // alt+f — favorite/star the focused panel's cursor row

/**
 * How an action's default binding is expressed:
 *  - `command` — a command-modified chord. The bare `key` char is combined with the user's chosen
 *    modifier at resolution time (alt+key / ctrl+key, or both). Rebindable to another char.
 *  - `plain` — a literal chord, unaffected by the modifier setting (e.g. a bare special key). Used
 *    for actions whose binding is intrinsically not a command chord.
 */
export type BindingSpec =
  | { readonly kind: 'command'; readonly key: string }
  | { readonly kind: 'plain'; readonly chord: KeyChord };

/** One action's definition: its id, default binding, a human description (for hint/settings UIs),
 * and whether the settings menu may rebind it. Modifier-only chords (digits) are never rebindable. */
export interface ActionDef {
  readonly id: ActionId;
  readonly default: BindingSpec;
  readonly description: string;
  readonly rebindable: boolean;
}

/** Sugar for a command-modified default. */
function command(key: string): BindingSpec {
  return { kind: 'command', key };
}

/**
 * The action table — the single source of truth for named chords. Mirrors today's behavior exactly:
 * the `key` chars are the current alt+<key> literals (alt+space, alt+s, alt+y, alt+p, alt+t, alt+f),
 * plus `global.settings` (alt+o / ctrl+o default). Adding a feature is one entry here.
 */
export const ACTIONS: Readonly<Record<ActionId, ActionDef>> = {
  'global.focusChat': {
    id: 'global.focusChat',
    default: command(' '),
    description: 'chat',
    rebindable: false,
  },
  'global.spawn': {
    id: 'global.spawn',
    default: command('s'),
    description: 'spawn',
    rebindable: true,
  },
  'global.tmux': {
    id: 'global.tmux',
    default: command('y'),
    description: 'tmux',
    rebindable: true,
  },
  'global.newPlan': {
    id: 'global.newPlan',
    default: command('p'),
    description: 'new plan',
    rebindable: true,
  },
  'global.newTicket': {
    id: 'global.newTicket',
    default: command('t'),
    description: 'new ticket',
    rebindable: true,
  },
  'global.settings': {
    id: 'global.settings',
    // Default: alt+o / ctrl+o. WHY NOT ',' (the original plan-locked default): Ink's legacy keypress
    // parser (`parse-keypress.js`, `metaKeyCodeRe = /^\x1b([a-zA-Z0-9])$/`) only sets `key.meta` for
    // an ESC-prefixed *alphanumeric* — an ESC-prefixed punctuation byte (alt+,) parses as a bare `,`
    // with `meta:false`, so alt+, was UNREACHABLE on the legacy/alt path (live finding). An
    // alphanumeric key avoids that: alt+o gets `key.meta` from the legacy parser, and ctrl+o's byte
    // (0x0f) is a clean control byte the shim/parser deliver as `{ctrl, input:'o'}` — so the menu is
    // reachable under both the alt and ctrl/kitty modifiers. 'o' (mnemonic: "open settings") is unused
    // by any other action and is not a meaningful panel-local plain key.
    default: command('o'),
    description: 'settings',
    rebindable: false,
  },
  'panel.star': {
    id: 'panel.star',
    default: command('f'),
    description: 'favorite',
    rebindable: true,
  },
};

/** Every action id, in declaration order — for iterating the settings menu / building hint tables. */
export const ACTION_IDS = Object.keys(ACTIONS) as readonly ActionId[];

/**
 * The resolved binding table for one modifier choice + override set. This is the deep interface
 * callers use: they pass an {@link ActionId} and an Ink event; they never see the modifier.
 */
export interface ResolvedBindings {
  /** The chord(s) `id` is bound to: one chord under `alt`/`ctrl`, two under `both` (alt + ctrl). */
  chordsFor(id: ActionId): readonly KeyChord[];
  /** True iff the Ink `(input, key)` event matches any chord bound to `id`. */
  matches(id: ActionId, input: string, key: Key): boolean;
  /** A short label for hint bars — `M-s` (alt), `C-s` (ctrl), `M-s/C-s` (both), or a plain key name. */
  label(id: ActionId): string;
  /** True iff `key` carries the command modifier (gates digit toggles + vim nav). Under `both`,
   * either alt or ctrl qualifies. */
  isCommandModified(key: Key): boolean;
}

/** The concrete modifiers a `command` action expands to under each {@link Modifier} choice (after
 * degradation). `alt` → `meta`; `ctrl` → `ctrl`; `both` → both. */
type CommandFlag = 'meta' | 'ctrl';

/** Which command flags a modifier choice maps to, after ctrl-availability degradation. */
function commandFlags(modifier: Modifier, ctrlAvailable: boolean): readonly CommandFlag[] {
  // ctrl is only honoured when the terminal can deliver it; otherwise it degrades to alt. `both`
  // keeps alt and adds ctrl only when available.
  switch (modifier) {
    case 'alt':
      return ['meta'];
    case 'ctrl':
      return ctrlAvailable ? ['ctrl'] : ['meta'];
    case 'both':
      return ctrlAvailable ? ['meta', 'ctrl'] : ['meta'];
  }
}

/** Build the chord for one command flag + key char. */
function commandChord(flag: CommandFlag, key: string): KeyChord {
  return { input: key, key: { [flag]: true } };
}

/** The label prefix for a command flag (`M-` alt, `C-` ctrl). */
function flagPrefix(flag: CommandFlag): string {
  return flag === 'meta' ? 'M-' : 'C-';
}

/** Render a plain chord's key for a label: its printable char, else its first special-key flag. */
function plainLabel(chord: KeyChord): string {
  if (chord.input !== undefined) {
    return chord.input;
  }
  const flags = chord.key === undefined ? [] : Object.keys(chord.key);
  return flags[0] ?? '?';
}

/**
 * Resolve the full binding table for the given modifier choice, ctrl availability, and per-action
 * key overrides. Pure — call it whenever any of those change (the store does this) and hand the
 * result around. The returned object's identity is stable for a given input, so it is safe as a
 * `useMemo`/effect dependency (re-registering keymaps only when settings actually change).
 *
 * `overrides` maps an {@link ActionId} to a replacement key char for `command`-kind actions only
 * (the settings menu's rebinds). A `plain` action ignores overrides (its chord is intrinsic).
 *
 * @param modifier the user's command-modifier choice
 * @param ctrlAvailable whether the terminal can deliver ctrl chords (kitty protocol); when false,
 *   `ctrl`/`both` degrade toward alt
 * @param overrides per-action key-char replacements for `command` actions
 */
export function resolveBindings(
  modifier: Modifier,
  ctrlAvailable: boolean,
  overrides: Partial<Record<ActionId, string>>,
): ResolvedBindings {
  const flags = commandFlags(modifier, ctrlAvailable);

  // Resolve every action to its chord list once, so chordsFor/matches/label all read the same table.
  const table = {} as Record<ActionId, readonly KeyChord[]>;
  for (const id of ACTION_IDS) {
    const def = ACTIONS[id];
    if (def.default.kind === 'plain') {
      table[id] = [def.default.chord];
      continue;
    }
    const key = overrides[id] ?? def.default.key;
    table[id] = flags.map((flag) => commandChord(flag, key));
  }

  // Precompute labels alongside the chord table.
  const labels = {} as Record<ActionId, string>;
  for (const id of ACTION_IDS) {
    const def = ACTIONS[id];
    if (def.default.kind === 'plain') {
      labels[id] = plainLabel(def.default.chord);
      continue;
    }
    const key = overrides[id] ?? def.default.key;
    // A space key reads as `space` in the label (a literal ' ' would be invisible).
    const keyLabel = key === ' ' ? 'space' : key;
    labels[id] = flags.map((flag) => `${flagPrefix(flag)}${keyLabel}`).join('/');
  }

  return {
    chordsFor(id) {
      return table[id];
    },
    matches(id, input, key) {
      return table[id].some((chord) => chordMatchesEvent(chord, input, key));
    },
    label(id) {
      return labels[id];
    },
    isCommandModified(key) {
      return flags.some((flag) => key[flag] === true);
    },
  };
}

/** Local chord-vs-event predicate. Mirrors {@link ../input/keymap.js chordMatches} but kept private
 * here so `bindings` has no import cycle risk and the matching rule is co-located with resolution. */
function chordMatchesEvent(chord: KeyChord, input: string, key: Key): boolean {
  if (chord.input !== undefined && chord.input !== input) {
    return false;
  }
  if (chord.key !== undefined) {
    for (const flag of Object.keys(chord.key) as (keyof Key)[]) {
      if (chord.key[flag] && !key[flag]) {
        return false;
      }
    }
  }
  return true;
}

/** The default resolution — alt modifier, ctrl unavailable, no overrides. This is *today's* behavior
 * and the fallback the dispatcher uses when a context omits explicit bindings (zero-behavior-change
 * guarantee for existing call sites and tests). */
export const DEFAULT_BINDINGS: ResolvedBindings = resolveBindings('alt', false, {});
