/**
 * translate tests — the token→{legacy bytes | side-channel chord} decision table. The key
 * invariants: ctrl+c is ALWAYS literal \x03; legacy-representable keys synthesise the same bytes the
 * terminal would have sent without kitty; the unrepresentable command combos (ctrl+digit/space/i/m/h)
 * become chords (the unrepresentable command combos are ctrl+digit/space/i/m/h/j).
 */

import { describe, expect, it } from 'vitest';
import type { CsiKeyToken } from '../../src/terminal/csiU.js';
import { type Translation, translate } from '../../src/terminal/translate.js';

/** kitty mods param = bits + 1; build it from named modifiers. */
function mods(opts: { ctrl?: boolean; alt?: boolean; shift?: boolean }): number {
  let bits = 0;
  if (opts.shift) bits |= 1;
  if (opts.alt) bits |= 2;
  if (opts.ctrl) bits |= 4;
  return bits + 1;
}

function key(code: number, m?: number, event?: number): CsiKeyToken {
  return {
    type: 'key',
    code,
    ...(m !== undefined ? { mods: m } : {}),
    ...(event !== undefined ? { event } : {}),
  };
}

function bytesOf(t: Translation): number[] {
  if (t.kind !== 'bytes') throw new Error(`expected bytes, got ${t.kind}`);
  return Array.from(t.bytes);
}

describe('ctrl+c is always literal ETX', () => {
  it('ctrl+c → \\x03', () => {
    expect(bytesOf(translate(key(0x63, mods({ ctrl: true }))))).toEqual([0x03]);
  });
  it('ctrl+shift+c still → \\x03', () => {
    expect(bytesOf(translate(key(0x63, mods({ ctrl: true, shift: true }))))).toEqual([0x03]);
  });
});

describe('esc', () => {
  it('esc → \\x1b', () => {
    expect(bytesOf(translate(key(27)))).toEqual([0x1b]);
  });
});

describe('plain printable keys → their UTF-8 bytes', () => {
  it('x → 0x78', () => {
    expect(bytesOf(translate(key(0x78)))).toEqual([0x78]);
  });
  it('1 → 0x31', () => {
    expect(bytesOf(translate(key(0x31)))).toEqual([0x31]);
  });
});

describe('alt+key → legacy ESC-prefixed (Ink reports key.meta)', () => {
  it('alt+x → ESC x', () => {
    expect(bytesOf(translate(key(0x78, mods({ alt: true }))))).toEqual([0x1b, 0x78]);
  });

  it('alt+shift+j → ESC J (shift carried as the shifted char, matching legacy terminals)', () => {
    // Kitty reports the unshifted codepoint + a shift bit; the legacy ESC form can only carry
    // shift as the uppercase char itself — the workspace `<Cmd>+Shift+J` chords depend on this.
    expect(bytesOf(translate(key(0x6a, mods({ alt: true, shift: true }))))).toEqual([0x1b, 0x4a]);
  });

  it('alt+shift+<caseless char> passes the char unchanged', () => {
    expect(bytesOf(translate(key(0x37, mods({ alt: true, shift: true }))))).toEqual([0x1b, 0x37]);
  });
});

describe('ctrl+letter → clean legacy control byte', () => {
  it('ctrl+a → 0x01', () => {
    expect(bytesOf(translate(key(0x61, mods({ ctrl: true }))))).toEqual([0x01]);
  });
  it('ctrl+s → 0x13', () => {
    expect(bytesOf(translate(key(0x73, mods({ ctrl: true }))))).toEqual([0x13]);
  });
  it('ctrl+o → 0x0f (clean byte; the settings default)', () => {
    expect(bytesOf(translate(key(0x6f, mods({ ctrl: true }))))).toEqual([0x0f]);
  });
  it('ctrl+S (uppercase) normalises to 0x13', () => {
    expect(bytesOf(translate(key(0x53, mods({ ctrl: true }))))).toEqual([0x13]);
  });
});

describe('ctrl+shift+<letter> → side-channel chord (shift has no legacy byte)', () => {
  // Workspace <Cmd>+Shift+<letter> chords under the ctrl/kitty modifier: ctrl+shift+<letter>
  // collapses to the same control byte as ctrl+<letter>, so shift must ride the side channel or it
  // is lost. Regression guard for workspace.prev (ctrl+shift+k) being unreachable.
  it('ctrl+shift+k → chord { input:"k", ctrl, shift } (workspace.prev)', () => {
    const t = translate(key(0x6b, mods({ ctrl: true, shift: true })));
    expect(t).toEqual({
      kind: 'chord',
      chord: { input: 'k', ctrl: true, alt: false, shift: true },
    });
  });
  it('ctrl+shift+j → chord { input:"j", ctrl, shift } (workspace.next; via the plain-chord route)', () => {
    const t = translate(key(0x6a, mods({ ctrl: true, shift: true })));
    expect(t).toEqual({
      kind: 'chord',
      chord: { input: 'j', ctrl: true, alt: false, shift: true },
    });
  });
  it('kitty reports the UNSHIFTED codepoint, so the chord char is lowercase even for uppercase input', () => {
    // A terminal that (wrongly) sent the shifted codepoint 0x4b still normalises to lowercase 'k'.
    const t = translate(key(0x4b, mods({ ctrl: true, shift: true })));
    expect(t).toEqual({
      kind: 'chord',
      chord: { input: 'k', ctrl: true, alt: false, shift: true },
    });
  });
  it('bare ctrl+k (no shift) is untouched — still the clean legacy byte 0x0b', () => {
    expect(bytesOf(translate(key(0x6b, mods({ ctrl: true }))))).toEqual([0x0b]);
  });
});

describe('unrepresentable command combos → side-channel chord', () => {
  it('ctrl+1 → chord { input:1, ctrl }', () => {
    const t = translate(key(0x31, mods({ ctrl: true })));
    expect(t).toEqual({
      kind: 'chord',
      chord: { input: '1', ctrl: true, alt: false, shift: false },
    });
  });
  it('ctrl+0 → chord { input:0, ctrl }', () => {
    const t = translate(key(0x30, mods({ ctrl: true })));
    expect(t).toEqual({
      kind: 'chord',
      chord: { input: '0', ctrl: true, alt: false, shift: false },
    });
  });
  it('ctrl+space → chord { input: " ", ctrl }', () => {
    const t = translate(key(0x20, mods({ ctrl: true })));
    expect(t).toEqual({
      kind: 'chord',
      chord: { input: ' ', ctrl: true, alt: false, shift: false },
    });
  });
  it('ctrl+i → chord with special name "tab" (collides with Tab byte)', () => {
    const t = translate(key(0x69, mods({ ctrl: true })));
    expect(t).toEqual({
      kind: 'chord',
      chord: { input: 'tab', ctrl: true, alt: false, shift: false },
    });
  });
  it('ctrl+m → chord "return"', () => {
    const t = translate(key(0x6d, mods({ ctrl: true })));
    expect(t).toEqual({
      kind: 'chord',
      chord: { input: 'return', ctrl: true, alt: false, shift: false },
    });
  });
  it('ctrl+h → chord { input:"h" } (byte 0x08 is `backspace` to Ink, not ctrl+h)', () => {
    // Like ctrl+j, ctrl+h carries its PLAIN char `h` (not the `backspace` special name): its dispatch
    // targets are the letter h (vim-nav left + global.cycleTargetPrev), and Ink would otherwise report
    // byte 0x08 as `backspace`.
    const t = translate(key(0x68, mods({ ctrl: true })));
    expect(t).toEqual({
      kind: 'chord',
      chord: { input: 'h', ctrl: true, alt: false, shift: false },
    });
  });
  it('ctrl+j → chord { input:"j" } (byte 0x0a is `enter` to Ink, not ctrl+j)', () => {
    // Unlike i/m, ctrl+j carries its PLAIN char `j` (not a special-key name): its dispatch target
    // is the letter (vim-nav down), and Ink would otherwise report byte 0x0a as `enter`/`return`.
    const t = translate(key(0x6a, mods({ ctrl: true })));
    expect(t).toEqual({
      kind: 'chord',
      chord: { input: 'j', ctrl: true, alt: false, shift: false },
    });
  });
});

describe('modified Enter (0x0d) → side-channel chord (chat-input overhaul)', () => {
  it('shift+enter → chord "return" with shift (newline insertion)', () => {
    // The whole reason for the change: a bare 0x0d byte cannot carry shift, so a modified Enter must
    // ride the side channel to reach the chat field as { return:true, shift:true }.
    const t = translate(key(13, mods({ shift: true })));
    expect(t).toEqual({
      kind: 'chord',
      chord: { input: 'return', ctrl: false, alt: false, shift: true },
    });
  });
  it('plain Enter (no modifier) stays the legacy CR byte (Ink return path untouched)', () => {
    const t = translate(key(13, undefined));
    expect(t).toEqual({ kind: 'bytes', bytes: Uint8Array.from([0x0d]) });
  });
  it('ctrl+Enter → chord { return, ctrl } (same as ctrl+m, the murder arm)', () => {
    const t = translate(key(13, mods({ ctrl: true })));
    expect(t).toEqual({
      kind: 'chord',
      chord: { input: 'return', ctrl: true, alt: false, shift: false },
    });
  });
});

describe('Backspace CSI-u translation', () => {
  it('plain DEL-style Backspace (127) stays usable as legacy DEL', () => {
    expect(bytesOf(translate(key(127)))).toEqual([0x7f]);
  });

  it('plain BS-style Backspace (8) is normalized to legacy DEL', () => {
    expect(bytesOf(translate(key(8)))).toEqual([0x7f]);
  });

  it('modified Backspace travels the side channel with modifiers intact', () => {
    const t = translate(key(127, mods({ alt: true })));
    expect(t).toEqual({
      kind: 'chord',
      chord: { input: 'backspace', ctrl: false, alt: true, shift: false },
    });
  });
});

describe('events', () => {
  it('drops a release event (event 3) → empty bytes', () => {
    const t = translate(key(0x73, mods({ ctrl: true }), 3));
    expect(bytesOf(t)).toEqual([]);
  });
  it('honours an explicit press event (event 1)', () => {
    const t = translate(key(0x73, mods({ ctrl: true }), 1));
    expect(bytesOf(t)).toEqual([0x13]);
  });
});
