import { describe, expect, it } from 'vitest';
import { packHints } from '../../src/components/BottomBar.js';
import type { BottomBarHint } from '../../src/selectors/barSelectors.js';

/**
 * packHints is the load-bearing piece of the portrait Body-height fix (L4c-fix2): the Shell derives
 * the portrait Body height from `useBottomBarLines().length`, and the BottomBar renders that same
 * line count as explicit rows. If the packing here ever disagreed with the render, the bottom rail
 * strip would clip into / overflow the chat input. These lock the packing contract.
 */
const h = (key: string, description: string): BottomBarHint => ({ key, description });
// width of a hint = key + ' ' + description; HINT_GAP = 2 between hints on a line.
const w = (hint: BottomBarHint): number => hint.key.length + 1 + hint.description.length;

describe('packHints', () => {
  it('returns no lines for no hints', () => {
    expect(packHints([], 80)).toEqual([]);
  });

  it('keeps everything on one line when it fits', () => {
    const hints = [h('a', 'bb'), h('c', 'dd')];
    const lines = packHints(hints, 80);
    expect(lines).toHaveLength(1);
    expect(lines[0]).toEqual(hints);
  });

  it('wraps to a new line when the next hint (plus the gap) would overflow', () => {
    const a = h('a', 'bb'); // width 4
    const b = h('c', 'dd'); // width 4
    // 4 + GAP(2) + 4 = 10 fits exactly at avail 10, but not at 9.
    expect(packHints([a, b], 10)).toHaveLength(1);
    const wrapped = packHints([a, b], 9);
    expect(wrapped).toHaveLength(2);
    expect(wrapped[0]).toEqual([a]);
    expect(wrapped[1]).toEqual([b]);
  });

  it('never drops a hint wider than the available width — it gets its own line', () => {
    const big = h('m', 'toggle maximized'); // width > 10
    expect(w(big)).toBeGreaterThan(10);
    const lines = packHints([h('s', 'star'), big, h('r', 'go')], 10);
    // every hint is present exactly once, across however many lines
    expect(lines.flat()).toHaveLength(3);
    // the oversized hint occupies a line by itself
    expect(lines.some((line) => line.length === 1 && line[0] === big)).toBe(true);
  });

  it('each produced line fits the width (except a lone oversized hint)', () => {
    const hints = [
      h('alt+1–0', 'panels'),
      h('alt+hjkl', 'nav'),
      h('alt+f', 'chat'),
      h('j', 'next crow'),
      h('k', 'prev crow'),
      h('r', 'refresh'),
      h('m', 'toggle maximized'),
      h('s', 'star'),
    ];
    const avail = 60;
    for (const line of packHints(hints, avail)) {
      const used = line.reduce((sum, hint) => sum + w(hint), 0) + 2 * (line.length - 1);
      if (line.length > 1) {
        expect(used).toBeLessThanOrEqual(avail);
      }
    }
  });
});
