import { describe, expect, it } from 'vitest';
import { truncateToWidth, wrapTextToRows } from '../../src/utils/wrapText.js';

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

  it('sanitizes controls before wrapping', () => {
    // `\r` becomes a newline, so the wrap sees two logical lines.
    expect(wrapTextToRows('ab\rcdef', 4, { hard: true, wordWrap: false })).toEqual(['ab', 'cdef']);
    expect(wrapTextToRows('ab\bcdef', 4, { hard: true, wordWrap: false })).toEqual(['abcd', 'ef']);
  });
});

describe('truncateToWidth — cookbook', () => {
  it('returns a single hard-clamped row', () => {
    expect(truncateToWidth('abcdefghij', 4)).toBe('abcd');
  });
});
