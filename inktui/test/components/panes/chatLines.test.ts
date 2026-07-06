import { describe, expect, it } from 'vitest';
import {
  flattenTurns,
  wrapChatLines,
  type ChatLine,
} from '../../../src/components/panes/chatLines.js';
import type { ChatTurn } from '../../../src/selectors/conversationsSelectors.js';

function gutterFlags(lines: readonly ChatLine[]): ('head' | 'cont' | 'none' | 'blank')[] {
  return lines.map((line) => {
    if (line.kind === 'blank' && line.gutter === 'none') return 'none';
    if (line.kind === 'blank') return 'blank';
    return line.firstOfTurn ? 'head' : 'cont';
  });
}

describe('wrapChatLines — cookbook', () => {
  it('wraps a long prose line into continuation rows with ▏ gutters', () => {
    const base: ChatLine[] = [
      {
        speaker: 'assistant',
        kind: 'prose',
        text: 'alpha beta gamma delta epsilon',
        firstOfTurn: true,
      },
    ];
    const wrapped = wrapChatLines(base, 10);
    expect(wrapped.map((line) => line.text)).toEqual(['alpha beta', ' gamma ', 'delta ', 'epsilon']);
    expect(gutterFlags(wrapped)).toEqual(['head', 'cont', 'cont', 'cont']);
  });

  it('keeps firstOfTurn false on wrapped continuation rows', () => {
    const base: ChatLine[] = [
      {
        speaker: 'user',
        kind: 'prose',
        text: 'one two three four five six',
        firstOfTurn: false,
      },
    ];
    const wrapped = wrapChatLines(base, 8);
    expect(wrapped.every((line) => !line.firstOfTurn)).toBe(true);
    expect(gutterFlags(wrapped)).toEqual(['cont', 'cont', 'cont', 'cont']);
  });
});

describe('flattenTurns + wrapChatLines — merged visual runs', () => {
  it('uses ▏ on blank separators between same-speaker turns', () => {
    const turns: ChatTurn[] = [
      { speaker: 'assistant', text: 'first chunk', blockId: 'a1' },
      { speaker: 'assistant', text: 'second chunk', blockId: 'a2' },
    ];
    const lines = wrapChatLines(flattenTurns(turns), 40);
    const blank = lines.find((line) => line.kind === 'blank');
    expect(blank).toBeDefined();
    expect(blank?.firstOfTurn).toBe(false);
    expect(blank?.gutter).not.toBe('none');
    expect(gutterFlags(lines)).toContain('blank');
  });
});
