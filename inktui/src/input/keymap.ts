/**
 * Keymap-as-data — the canonical shape by which a panel **declares** its keys (rule 5: input is
 * data, not gating). This is the format every future panel copies, so it is built to make declaring
 * a keymap the path of least resistance and imperative key handling the awkward path.
 *
 * The old app put key handling in a central `check_action` table and per-widget `on_key` methods
 * with scattered conditionals — adding a panel meant editing the central table. Here a panel owns a
 * `Keymap`: a list of `{ chord, intent, description }`. The root dispatcher
 * (see {@link ./dispatcher.js}) is the *only* code that reads Ink key events; it matches them
 * against the focused panel's declared chords and fires the matched intent. A panel never calls
 * `useInput` and never sees a raw key — it provides data and an intent handler.
 *
 * `description` is mandatory, not decorative: the bottom bar renders contextual hints straight from
 * the focused panel's keymap (the plan's "Bottom bar: contextual hints"), so a declared key is a
 * self-documenting key.
 */

import type { Key } from 'ink';

/**
 * A key chord to match against an Ink `(input, key)` event. Fields are ANDed; an omitted field is
 * "don't care" — WITH ONE LOAD-BEARING EXCEPTION for the command modifiers (`ctrl`, `meta`). Two
 * shapes cover everything:
 *  - `input`: the printable char (`'j'`, `'1'`). Match is case-sensitive on the raw char.
 *  - `key`: required modifier/special-key flags (`{ ctrl: true }`, `{ return: true }`). A listed flag
 *    must be true; an unlisted *non-command* flag is ignored (so `{ key: { return: true } }` matches
 *    Enter regardless of e.g. `shift`). But `ctrl` and `meta` are NOT don't-care: a chord that does
 *    not list `ctrl` requires `ctrl:false`, and likewise for `meta` (see {@link chordMatches}). This
 *    is what stops a panel's plain letter (`{ input: 'x' }`) from silently absorbing its modified
 *    variants (`alt+x` / `ctrl+x`) — without it, correctness rests on dispatch ordering alone.
 *
 * At least one of `input`/`key` must be present (a chord matching nothing is meaningless); the type
 * enforces this with a union so an empty `{}` is a compile error.
 */
export type KeyChord =
  | { readonly input: string; readonly key?: Partial<Key> }
  | { readonly input?: string; readonly key: Partial<Key> };

/**
 * One declared binding: a chord, the intent it fires, and a human description for the hint bar.
 * `Intent` is the panel's own action-name union (a string union, e.g. `'open' | 'star'`), so the
 * panel's intent handler is exhaustively typed against its own keymap.
 */
export interface KeymapEntry<Intent extends string> {
  /** The chord(s) that fire this intent. A single {@link KeyChord}, or a list (any of which matches)
   * — the list form lets a binding resolved from the registry (see {@link ./bindings.js}) bind the
   * same intent to more than one chord, e.g. alt+key AND ctrl+key under the `both` modifier. */
  readonly chord: KeyChord | readonly KeyChord[];
  readonly intent: Intent;
  readonly description: string;
}

/** A panel's full keymap: its declared bindings. Order matters only for first-match-wins on an
 * (unlikely) overlapping chord; otherwise it is just the panel's binding list. */
export type Keymap<Intent extends string> = readonly KeymapEntry<Intent>[];

/** True if an Ink `(input, key)` event satisfies `chord` (all present fields match). The single
 * matching predicate the dispatcher uses, exported so a panel's test can assert its keymap matches
 * the keys it means to without standing up the whole dispatcher.
 *
 * `ctrl`/`meta` are matched STRICTLY: a chord that does not explicitly list one of them requires
 * that flag to be FALSE in the event. This makes a plain chord assert `ctrl:false, meta:false` by
 * default, so `{ input: 'x' }` no longer absorbs `alt+x`/`ctrl+x`. Every other unlisted flag stays
 * don't-care (so `{ key: { return: true } }` still matches Enter regardless of `shift`). */
export function chordMatches(chord: KeyChord, input: string, key: Key): boolean {
  if (chord.input !== undefined && chord.input !== input) {
    return false;
  }
  // The command modifiers are never don't-care: an event carrying ctrl/meta the chord didn't ask for
  // must NOT match. (A chord opts into a modifier by listing it `true` in `chord.key`.)
  if (chord.key?.ctrl !== true && key.ctrl) {
    return false;
  }
  if (chord.key?.meta !== true && key.meta) {
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

/**
 * Find the intent a `(input, key)` event fires in `keymap`, or `null` if no chord matches. First
 * match wins. This is what the dispatcher calls after it has decided the event belongs to the
 * focused panel — pure over the keymap, so it tests without Ink.
 */
export function matchKeymap<Intent extends string>(
  keymap: Keymap<Intent>,
  input: string,
  key: Key,
): Intent | null {
  for (const entry of keymap) {
    // An entry's `chord` is a single chord or a list of equivalent chords (the `both`-modifier
    // expansion); the entry fires if ANY of its chords matches.
    const chords = Array.isArray(entry.chord) ? entry.chord : [entry.chord];
    if (chords.some((chord) => chordMatches(chord, input, key))) {
      return entry.intent;
    }
  }
  return null;
}

/**
 * What a panel registers with the dispatcher: its declared keymap plus the handler that runs the
 * matched intent. The handler is the panel's own — it closes over the panel's store actions / local
 * state. This pair (data + its interpreter) is the whole contract a panel implements to be
 * keyboard-driven; nothing else couples a panel to input.
 */
export interface PanelKeymap<Intent extends string = string> {
  readonly keymap: Keymap<Intent>;
  readonly onIntent: (intent: Intent) => void;
}
