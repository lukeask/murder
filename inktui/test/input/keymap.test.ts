/**
 * keymap-as-data tests — chord matching and first-match intent resolution, pure over synthesised
 * key events.
 */

import { describe, expect, it } from 'vitest';
import { chordMatches, type Keymap, matchKeymap } from '../../src/input/keymap.js';
import { makeKey } from './key.js';

describe('chordMatches', () => {
  it('matches on a printable char', () => {
    expect(chordMatches({ input: 'a' }, 'a', makeKey())).toBe(true);
    expect(chordMatches({ input: 'a' }, 'b', makeKey())).toBe(false);
  });

  it('matches on a required key flag, ignoring unlisted NON-command flags', () => {
    expect(chordMatches({ key: { return: true } }, '', makeKey({ return: true }))).toBe(true);
    expect(chordMatches({ key: { return: true } }, '', makeKey({ return: false }))).toBe(false);
    // A listed flag must be set; an unrelated non-command flag being set does not break the match.
    expect(
      chordMatches({ key: { return: true } }, '', makeKey({ return: true, shift: true })),
    ).toBe(true);
  });

  it('ANDs input and key flags', () => {
    const chord = { input: 's', key: { ctrl: true } };
    expect(chordMatches(chord, 's', makeKey({ ctrl: true }))).toBe(true);
    expect(chordMatches(chord, 's', makeKey({ ctrl: false }))).toBe(false);
  });

  it('treats ctrl/meta as STRICT: a plain chord rejects its modified variants', () => {
    // A bare panel letter must NOT absorb alt+x / ctrl+x (the matcher asserts ctrl:false, meta:false
    // unless the chord explicitly lists them — otherwise correctness rests on dispatch ordering).
    const plain = { input: 'x' };
    expect(chordMatches(plain, 'x', makeKey())).toBe(true);
    expect(chordMatches(plain, 'x', makeKey({ meta: true }))).toBe(false);
    expect(chordMatches(plain, 'x', makeKey({ ctrl: true }))).toBe(false);
    // A chord that lists a command modifier still requires it (and rejects the other).
    const alt = { input: 'x', key: { meta: true } };
    expect(chordMatches(alt, 'x', makeKey({ meta: true }))).toBe(true);
    expect(chordMatches(alt, 'x', makeKey({ ctrl: true }))).toBe(false);
  });
});

describe('matchKeymap', () => {
  const keymap: Keymap<'open' | 'star'> = [
    { chord: { key: { return: true } }, intent: 'open', description: 'open doc' },
    { chord: { input: 's' }, intent: 'star', description: 'star' },
  ];

  it('returns the matched intent', () => {
    expect(matchKeymap(keymap, '', makeKey({ return: true }))).toBe('open');
    expect(matchKeymap(keymap, 's', makeKey())).toBe('star');
  });

  it('returns null when nothing matches', () => {
    expect(matchKeymap(keymap, 'z', makeKey())).toBeNull();
  });
});
