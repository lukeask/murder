/**
 * csiU parser tests — the incremental CSI-u state machine. Covers: full and chunk-split sequences,
 * the load-bearing lone-ESC timeout flush, paste/text passthrough, and the CSI-u encodings for the
 * keys the translator cares about (esc, ctrl+c, alt+x, ctrl+1, ctrl+s). The parser is pure (no
 * timers): the lone-ESC flush is driven by an explicit {@link CsiUParser.flushPending} call.
 */

import { describe, expect, it } from 'vitest';
import { type CsiToken, CsiUParser } from '../../src/terminal/csiU.js';

/** Encode a string to the byte chunk the parser ingests. */
function bytes(s: string): Uint8Array {
  return new Uint8Array(Buffer.from(s, 'latin1'));
}

/** Feed a whole string and return the tokens. */
function feed(parser: CsiUParser, s: string): CsiToken[] {
  return parser.feed(bytes(s));
}

/** Collect all passthrough bytes from a token list as a latin1 string. */
function passthroughText(tokens: readonly CsiToken[]): string {
  let out = '';
  for (const t of tokens) {
    if (t.type === 'passthrough') {
      out += Buffer.from(t.bytes).toString('latin1');
    }
  }
  return out;
}

describe('plain text passthrough', () => {
  it('forwards ordinary text verbatim as a single passthrough token', () => {
    const p = new CsiUParser();
    const tokens = feed(p, 'hello world');
    expect(tokens).toEqual([{ type: 'passthrough', bytes: bytes('hello world') }]);
  });

  it('passes a paste (text containing no escape) straight through', () => {
    const p = new CsiUParser();
    const paste = 'a long pasted line with 12345 and symbols !@#$%';
    expect(passthroughText(feed(p, paste))).toBe(paste);
  });
});

describe('CSI-u keypress decoding', () => {
  it('decodes esc (CSI 27 u) as a key token', () => {
    const p = new CsiUParser();
    expect(feed(p, '\x1b[27u')).toEqual([{ type: 'key', code: 27 }]);
  });

  it('decodes ctrl+c (CSI 99 ; 5 u): code 99, mods 5', () => {
    const p = new CsiUParser();
    expect(feed(p, '\x1b[99;5u')).toEqual([{ type: 'key', code: 99, mods: 5 }]);
  });

  it('decodes alt+x (CSI 120 ; 3 u): code 120, mods 3', () => {
    const p = new CsiUParser();
    expect(feed(p, '\x1b[120;3u')).toEqual([{ type: 'key', code: 120, mods: 3 }]);
  });

  it('decodes ctrl+1 (CSI 49 ; 5 u): code 49, mods 5', () => {
    const p = new CsiUParser();
    expect(feed(p, '\x1b[49;5u')).toEqual([{ type: 'key', code: 49, mods: 5 }]);
  });

  it('decodes ctrl+s (CSI 115 ; 5 u): code 115, mods 5', () => {
    const p = new CsiUParser();
    expect(feed(p, '\x1b[115;5u')).toEqual([{ type: 'key', code: 115, mods: 5 }]);
  });

  it('decodes an event sub-parameter (CSI 115 ; 5 : 3 u → release)', () => {
    const p = new CsiUParser();
    expect(feed(p, '\x1b[115;5:3u')).toEqual([{ type: 'key', code: 115, mods: 5, event: 3 }]);
  });

  it('decodes a bare code with no modifiers (CSI 97 u)', () => {
    const p = new CsiUParser();
    expect(feed(p, '\x1b[97u')).toEqual([{ type: 'key', code: 97 }]);
  });
});

describe('chunk-split sequences', () => {
  it('reassembles a sequence split byte-by-byte', () => {
    const p = new CsiUParser();
    const seq = '\x1b[49;5u';
    const collected: CsiToken[] = [];
    for (const ch of seq) {
      collected.push(...feed(p, ch));
    }
    expect(collected).toEqual([{ type: 'key', code: 49, mods: 5 }]);
  });

  it('reassembles a sequence split at the ESC/[ boundary', () => {
    const p = new CsiUParser();
    expect(feed(p, '\x1b')).toEqual([]);
    expect(feed(p, '[99;5u')).toEqual([{ type: 'key', code: 99, mods: 5 }]);
  });

  it('handles text, then a split key, then more text in order', () => {
    const p = new CsiUParser();
    const a = feed(p, 'ab\x1b[49');
    const b = feed(p, ';5ucd');
    expect(a).toEqual([{ type: 'passthrough', bytes: bytes('ab') }]);
    expect(b).toEqual([
      { type: 'key', code: 49, mods: 5 },
      { type: 'passthrough', bytes: bytes('cd') },
    ]);
  });
});

describe('lone-ESC timeout flush (load-bearing)', () => {
  it('holds a lone ESC, then flushes it as a literal ESC byte on timeout', () => {
    const p = new CsiUParser();
    // A bare ESC produces no token yet (it might start a sequence).
    expect(feed(p, '\x1b')).toEqual([]);
    expect(p.hasPending()).toBe(true);
    // On the timer firing, the shim calls flushPending → the ESC is emitted as passthrough.
    const flushed = p.flushPending();
    expect(flushed).toEqual([{ type: 'passthrough', bytes: bytes('\x1b') }]);
    expect(p.hasPending()).toBe(false);
  });

  it('does NOT flush when the sequence completes before the timeout', () => {
    const p = new CsiUParser();
    feed(p, '\x1b');
    const done = feed(p, '[27u');
    expect(done).toEqual([{ type: 'key', code: 27 }]);
    // Nothing pending → a later flush is a no-op.
    expect(p.hasPending()).toBe(false);
    expect(p.flushPending()).toEqual([]);
  });

  it('flushes a stalled partial sequence as passthrough (recovery, not swallow)', () => {
    const p = new CsiUParser();
    feed(p, '\x1b[49;5'); // never received the final 'u'
    expect(p.hasPending()).toBe(true);
    expect(p.flushPending()).toEqual([{ type: 'passthrough', bytes: bytes('\x1b[49;5') }]);
  });
});

describe('protocol replies (swallowed, not passthrough)', () => {
  it('decodes the kitty query reply CSI ? <flags> u', () => {
    const p = new CsiUParser();
    expect(feed(p, '\x1b[?1u')).toEqual([{ type: 'queryReply', flags: 1 }]);
  });

  it('decodes a query reply with no flags as flags 0', () => {
    const p = new CsiUParser();
    expect(feed(p, '\x1b[?u')).toEqual([{ type: 'queryReply', flags: 0 }]);
  });

  it('decodes a DA1 reply CSI ? ... c', () => {
    const p = new CsiUParser();
    expect(feed(p, '\x1b[?62;c')).toEqual([{ type: 'daReply' }]);
  });
});

describe('non-kitty escapes pass through verbatim', () => {
  it('forwards a cursor-position-style CSI sequence unchanged', () => {
    const p = new CsiUParser();
    // CSI 1 ; 1 R (cursor report) — not a kitty key (final 'R'), so passthrough byte-for-byte.
    expect(passthroughText(feed(p, '\x1b[1;1R'))).toBe('\x1b[1;1R');
  });

  it('forwards an ESC not followed by [ verbatim', () => {
    const p = new CsiUParser();
    expect(passthroughText(feed(p, '\x1bOP'))).toBe('\x1bOP');
  });
});
