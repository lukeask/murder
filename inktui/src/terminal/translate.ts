/**
 * `translate` — turn a decoded kitty {@link CsiKeyToken} into one of two outcomes:
 *
 *  1. **Legacy bytes** the downstream consumer (Ink's own input parser) already understands, so the
 *     keypress behaves exactly as it does without the kitty protocol. Esc → `\x1b`, ctrl+c →
 *     `\x03` (literal — so Ink's `exitOnCtrlC` keeps working), a printable char → its UTF-8 bytes,
 *     alt+<key> → the legacy ESC-prefixed form (Ink reports it as `key.meta`, unchanged from today),
 *     and a ctrl+<letter> that has a clean legacy control byte → that byte.
 *
 *  2. **A side-channel {@link Chord}** for combos legacy encoding *cannot* represent — ctrl+digit,
 *     ctrl+i/m/h/j (collide with Tab/Enter/Backspace/Enter), ctrl+space. These never go downstream as bytes
 *     (there are no bytes that mean them); instead the shim emits a `chord` event the dispatcher
 *     subscribes to. ctrl+i/m carry the special-key NAME they collide with (Tab/Enter); ctrl+h/j carry
 *     the plain *letter* (their dispatch target is the vim-nav letter, not the special key). This is the
 *     whole reason for the kitty opt-in: these chords are the new command space.
 *
 * The split keeps the change behavior-neutral for everything legacy already handled (we synthesise
 * the same bytes the terminal would have sent without kitty) and additive for everything it could
 * not. The decision lives here as a pure function table so it is exhaustively testable.
 *
 * This module has NO Ink import — it deals in bytes and a plain {@link Chord} record.
 */

import type { CsiKeyToken } from './csiU.js';

/** Kitty modifier bitmask values (the wire param is `bits + 1`; see {@link decodeMods}). Only the
 * three we act on are named — shift/super/hyper/meta/lock pass through transparently because we only
 * branch on ctrl/alt for the legacy-vs-chord decision. */
const MOD_SHIFT = 1;
const MOD_ALT = 2;
const MOD_CTRL = 4;

/** A side-channel chord — a command combo with no legacy byte representation. The dispatcher matches
 * it the same way it matches an Ink key event: by the base key (`input`) plus the modifier flags. The
 * shape mirrors the relevant subset of Ink's `Key` so the existing matching logic applies unchanged. */
export interface Chord {
  /** The base key as a printable char where one exists (`'1'`, ` ` for space, `'j'` for ctrl+j,
   * `'h'` for ctrl+h), else the special-key name (`'tab'`, `'return'` for the ctrl+i/m collisions). */
  readonly input: string;
  readonly ctrl: boolean;
  readonly alt: boolean;
  readonly shift: boolean;
}

/** The result of translating one key token. */
export type Translation =
  | { readonly kind: 'bytes'; readonly bytes: Uint8Array }
  | { readonly kind: 'chord'; readonly chord: Chord };

/** Decode the raw kitty modifier param (1-based) into a flag set. A missing/zero param means no
 * modifiers. */
function decodeMods(mods: number | undefined): { ctrl: boolean; alt: boolean; shift: boolean } {
  const bits = mods === undefined || mods <= 0 ? 0 : mods - 1;
  return {
    ctrl: (bits & MOD_CTRL) !== 0,
    alt: (bits & MOD_ALT) !== 0,
    shift: (bits & MOD_SHIFT) !== 0,
  };
}

/** Ctrl+letter codepoints whose clean legacy control byte already means a *special* key, so Ink's
 * parser reports that special key (NOT `{ctrl, input:<letter>}`): i (Tab, 0x09), m (Enter/return,
 * 0x0d). These map to the special-key NAME the dispatcher would otherwise see, so a binding can
 * target them explicitly via the side channel. (h also collides — Backspace, 0x08 — but its dispatch
 * target is the *letter* h, so it lives in {@link CTRL_LETTER_PLAIN_CHORD} alongside j, not here.)
 *
 * AUDIT (vs `node_modules/ink`'s parse-keypress over bytes 0x01–0x1a): exactly four ctrl+letters do
 * NOT round-trip to `{ctrl:true, input:<letter>}` — i/m hit the special-key branches above, h (byte
 * 0x08 Backspace) and j (byte 0x0A `\n`, reported by Ink as `name:'enter'` with `ctrl:false`) are
 * handled separately (see {@link CTRL_LETTER_PLAIN_CHORD}) because their dispatch targets are the
 * *letters* h/j (vim-nav left/down — and ctrl+h is also `global.cycleTargetPrev`), not special keys,
 * so their chords carry the printable char, not a special name. ctrl+c/d/z are deliberately left as
 * literal control bytes (exit/EOF/SIGTSTP passthrough) and are not audited here. All other
 * ctrl+letters (a,b,e,f,g,k,l,n,o,p,q,r,s,t,u,v,w,x,y) are clean and stay legacy bytes. */
const CTRL_LETTER_COLLISIONS: Readonly<Record<number, string>> = {
  105: 'tab', // ctrl+i ≡ Tab (0x09)
  109: 'return', // ctrl+m ≡ Enter (0x0d)
};

/** Ctrl+letter codepoints whose legacy byte Ink reports as a special key, but whose dispatch target
 * is the *letter itself* (so the chord must carry the printable char, not a special-key name): j and
 * h. byte 0x0A (`\n`) → Ink `name:'enter', ctrl:false`; byte 0x08 → Ink `name:'backspace'`. Routing
 * these as plain-char chords restores ctrl+j (vim-nav down) and ctrl+h (vim-nav left +
 * `global.cycleTargetPrev` when chat is focused) — which a special-key name would shadow. */
const CTRL_LETTER_PLAIN_CHORD: ReadonlySet<number> = new Set([
  0x6a, // ctrl+j ≡ Enter byte (0x0a); routed as chord { input:'j', ctrl } for vim-nav down
  0x68, // ctrl+h ≡ Backspace byte (0x08); routed as chord { input:'h', ctrl } for vim-nav left + cycleTargetPrev
]);

/** Codepoint of the printable base key, when it is one (letters, digits, space, punctuation in the
 * Latin-1 printable ranges). Used to build the chord's `input` char. */
function printableChar(code: number): string | null {
  // Space + the printable ASCII range, plus Latin-1 printable. Excludes C0/C1 controls.
  if (code === 0x20 || (code >= 0x21 && code <= 0x7e) || (code >= 0xa0 && code <= 0x10ffff)) {
    return String.fromCodePoint(code);
  }
  return null;
}

/**
 * Translate one decoded kitty keypress. The decision tree, in order:
 *
 *  - **Release/repeat events** other than a press are dropped (empty bytes) — legacy input has no
 *    notion of them and nothing downstream wants them. (event 3 = release; 2 = repeat.)
 *  - **ctrl+c** → literal `\x03`, ALWAYS, regardless of other modifiers, so Ink's ctrl-c exit path is
 *    untouched.
 *  - **Esc** (code 27, no mods) → `\x1b`.
 *  - **ctrl + digit / space** → side-channel chord (no legacy byte exists).
 *  - **ctrl + i/m** → side-channel chord with the collision's special-key name (the legacy byte is
 *    ambiguous with Tab/Enter, so we must not emit it as a ctrl chord).
 *  - **ctrl + j/h** → side-channel chord with the plain char (`j`/`h`). Their legacy bytes are
 *    reported by Ink as `enter` (0x0A) / `backspace` (0x08), never `{ctrl, input:<letter>}`; the
 *    chord restores ctrl+j (vim-nav down) and ctrl+h (vim-nav left + cycleTargetPrev when chat
 *    is focused).
 *  - **ctrl + other letter** → the clean legacy control byte (`ctrl+a` → 0x01, …).
 *  - **alt + key** → legacy ESC-prefixed form (`ESC <char>`); Ink reports `key.meta`, as today.
 *  - **plain printable** → its UTF-8 bytes.
 *  - anything else with no representation → empty bytes (dropped).
 */
export function translate(token: CsiKeyToken): Translation {
  const { code, mods, event } = token;
  // Only act on key-press events; repeats/releases are noise to a legacy pipeline.
  if (event !== undefined && event !== 1) {
    return EMPTY;
  }
  const { ctrl, alt, shift } = decodeMods(mods);

  // ctrl+c — literal ETX, always (keeps Ink's exitOnCtrlC working under the protocol).
  if (ctrl && code === 0x63) {
    return bytesOf(0x03);
  }

  // Esc.
  if (code === 27 && !ctrl && !alt) {
    return bytesOf(0x1b);
  }

  // Enter (0x0d). A *modified* Enter (shift/alt/ctrl) has no legacy byte that preserves the modifier —
  // a bare 0x0d loses it, so the legacy/printable paths below would drop it entirely (code 13 is not
  // printable). Route it through the side channel like the ctrl+m collision, so it reaches the
  // dispatcher as `{ return: true, <mods> }`. This is what makes **shift+enter** arrive at the chat
  // field as `{ return:true, shift:true }` for newline insertion (chat-input overhaul, user ask #1).
  // Plain Enter (no modifier) stays the legacy CR byte so Ink's normal return path is untouched.
  // (ctrl+Enter resolves to `{ ctrl, return }`, the same chord as ctrl+m — the existing murder arm.)
  if (code === 13) {
    if (shift || alt || ctrl) {
      return chordOf('return', { ctrl, alt, shift });
    }
    return bytesOf(0x0d);
  }

  if (ctrl) {
    // ctrl+digit and ctrl+space have no legacy encoding → side channel.
    if ((code >= 0x30 && code <= 0x39) || code === 0x20) {
      return chordOf(code === 0x20 ? ' ' : String.fromCodePoint(code), { ctrl, alt, shift });
    }
    // ctrl + i/m collide with Tab/Enter bytes → side channel with the special name.
    const collision = CTRL_LETTER_COLLISIONS[code];
    if (collision !== undefined) {
      return chordOf(collision, { ctrl, alt, shift });
    }
    // ctrl + j/h collide with Enter/Backspace bytes but dispatch as the letter → side channel as the char.
    if (CTRL_LETTER_PLAIN_CHORD.has(code)) {
      return chordOf(String.fromCodePoint(code), { ctrl, alt, shift });
    }
    // ctrl + a..z (excluding the collisions) → clean legacy control byte (0x01..0x1a).
    if (code >= 0x61 && code <= 0x7a) {
      return bytesOf(code - 0x60);
    }
    // ctrl + uppercase letter — normalise to the same control byte.
    if (code >= 0x41 && code <= 0x5a) {
      return bytesOf(code - 0x40);
    }
    // Any other ctrl combo we can't represent legacy-side → side channel by its printable char.
    const printable = printableChar(code);
    if (printable !== null) {
      return chordOf(printable, { ctrl, alt, shift });
    }
    return EMPTY;
  }

  // alt+<char> → legacy meta form: ESC then the char's bytes (Ink decodes this as key.meta).
  if (alt) {
    const printable = printableChar(code);
    if (printable !== null) {
      return bytesOf(0x1b, ...utf8(printable));
    }
    return EMPTY;
  }

  // Plain printable key → its UTF-8 bytes.
  const printable = printableChar(code);
  if (printable !== null) {
    return bytesOf(...utf8(printable));
  }
  return EMPTY;
}

const EMPTY: Translation = { kind: 'bytes', bytes: new Uint8Array(0) };

function bytesOf(...bytes: number[]): Translation {
  return { kind: 'bytes', bytes: Uint8Array.from(bytes) };
}

function chordOf(
  input: string,
  flags: { ctrl: boolean; alt: boolean; shift: boolean },
): Translation {
  return { kind: 'chord', chord: { input, ...flags } };
}

const encoder = new TextEncoder();
function utf8(s: string): number[] {
  return Array.from(encoder.encode(s));
}
