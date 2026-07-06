import { describe, expect, it } from 'vitest';
import { wrapTextToRows } from '../../src/utils/wrapText.js';

describe('wrapTextToRows — cookbook', () => {
  it('splits prose on spaces at the column width', () => {
    expect(wrapTextToRows('one two three four', 8)).toEqual(['one two ', 'three ', 'four']);
  });

  it('returns a single row when text already fits', () => {
    expect(wrapTextToRows('short', 20)).toEqual(['short']);
  });

  it('hard-wraps long tokens without spaces', () => {
    expect(wrapTextToRows('abcdefghij', 4, { hard: true, wordWrap: false })).toEqual([
      'abcd',
      'efgh',
      'ij',
    ]);
  });
});
