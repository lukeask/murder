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

  it('matches on a required key flag, ignoring unlisted flags', () => {
    expect(chordMatches({ key: { return: true } }, '', makeKey({ return: true }))).toBe(true);
    expect(chordMatches({ key: { return: true } }, '', makeKey({ return: false }))).toBe(false);
    // A listed flag must be set; an unrelated flag being set does not break the match.
    expect(
      chordMatches({ key: { return: true } }, '', makeKey({ return: true, shift: true })),
    ).toBe(true);
  });

  it('ANDs input and key flags', () => {
    const chord = { input: 's', key: { ctrl: true } };
    expect(chordMatches(chord, 's', makeKey({ ctrl: true }))).toBe(true);
    expect(chordMatches(chord, 's', makeKey({ ctrl: false }))).toBe(false);
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
