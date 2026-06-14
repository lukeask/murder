/**
 * useGotoLine test — the pure core of the vim-style `g<digits>` go-to-line gesture (the keymap the
 * gesture declares per capture state, and the {@link reduceGoto} state machine). The component wiring
 * (jump → window move, title indicator) is covered by the DocPane/Stage integration tests.
 */

import { describe, expect, it } from 'vitest';
import { gotoKeymap, reduceGoto } from '../../src/hooks/useGotoLine.js';

describe('gotoKeymap — the entries per capture state', () => {
  it('idle: the visible `g` starter plus the PRE-REGISTERED (hidden, inert) digits', () => {
    const entries = gotoKeymap(null);
    // The digits ride along while idle so a fast `g3` arriving in ONE stdin chunk still lands —
    // keymap re-registration happens a render after `g`, so the `3` must already be declared.
    expect(entries).toHaveLength(11);
    expect(entries[0]?.intent).toBe('goto.start');
    expect(entries[0]?.chord).toEqual({ input: 'g' });
    expect(entries[0]?.hidden).toBeUndefined();
    for (const entry of entries.slice(1)) {
      expect(entry.hidden).toBe(true);
    }
    const intents = entries.map((entry) => entry.intent);
    expect(intents).toContain('goto.digit.0');
    expect(intents).toContain('goto.digit.9');
    // No end chords while idle: `enter`/`esc` must keep the pane's own meanings (e.g. close).
    expect(intents).not.toContain('goto.end');
  });

  it('capturing: declares the ten digits + the end chords, all hidden from the hint bar', () => {
    const entries = gotoKeymap('3');
    // 10 digits + the end entry.
    expect(entries).toHaveLength(11);
    for (const entry of entries) {
      expect(entry.hidden).toBe(true);
    }
    const intents = entries.map((entry) => entry.intent);
    expect(intents).toContain('goto.digit.0');
    expect(intents).toContain('goto.digit.9');
    expect(intents).toContain('goto.end');
    // `g`, `esc`, and `enter` all end the capture (so a live capture's enter never reaches the
    // pane's own close binding — the entries are spread ahead of the pane's).
    const end = entries.find((entry) => entry.intent === 'goto.end');
    expect(end?.chord).toEqual([
      { input: 'g' },
      { key: { escape: true } },
      { key: { return: true } },
    ]);
  });
});

describe('reduceGoto — the capture state machine', () => {
  it('start opens an empty capture without jumping', () => {
    expect(reduceGoto(null, 'goto.start')).toEqual({ pending: '', jumpTo: null });
  });

  it('digits extend the pending number and jump live (g39 = jump 3, then refine to 39)', () => {
    expect(reduceGoto('', 'goto.digit.3')).toEqual({ pending: '3', jumpTo: 3 });
    expect(reduceGoto('3', 'goto.digit.9')).toEqual({ pending: '39', jumpTo: 39 });
  });

  it('a lone 0 clamps to line 1 (lines are 1-based)', () => {
    expect(reduceGoto('', 'goto.digit.0')).toEqual({ pending: '0', jumpTo: 1 });
  });

  it('end closes the capture without jumping (position keeps the last live jump)', () => {
    expect(reduceGoto('39', 'goto.end')).toEqual({ pending: null, jumpTo: null });
  });

  it('a digit with NO live capture is inert (consumed, no jump) — the idle pre-registration case', () => {
    expect(reduceGoto(null, 'goto.digit.7')).toEqual({ pending: null, jumpTo: null });
  });

  it('returns null for a non-goto intent (the pane handles it and ends the capture via clear)', () => {
    expect(reduceGoto('3', 'scrollDown')).toBeNull();
    expect(reduceGoto(null, 'close')).toBeNull();
  });
});
