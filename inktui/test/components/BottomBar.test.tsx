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
// width of a hint = key + ' ' + description; HINT_GAP = 1 between hints on a line.
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
    // 4 + GAP(1) + 4 = 9 fits exactly at avail 9, but not at 8.
    expect(packHints([a, b], 9)).toHaveLength(1);
    const wrapped = packHints([a, b], 8);
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

  it('pins a right-aligned hint to the last line, after the left flow (item 12 prep)', () => {
    const right: BottomBarHint = { key: 'alt+/', description: 'help', align: 'right' };
    const lines = packHints([h('a', 'bb'), h('c', 'dd'), right], 80);
    // Everything fits on one line; the right hint trails the left ones.
    expect(lines).toHaveLength(1);
    expect(lines[0]?.at(-1)).toEqual(right);
    // It is detectable as right-aligned so the renderer can space-between it.
    expect(lines[0]?.some((hint) => hint.align === 'right')).toBe(true);
  });

  it('joins the right-aligned hint to the last wrapped line when it still fits', () => {
    const right: BottomBarHint = { key: '?', description: '', align: 'right' }; // width 1
    // Left wraps to two lines; the slim right hint still fits after the final line's flow.
    const lines = packHints([h('a', 'bb'), h('c', 'dd'), right], 8);
    expect(lines).toHaveLength(2);
    expect(lines.at(-1)).toContain(right);
  });

  it('drops the right-aligned hint to its own line when it would collide with the last line', () => {
    const right: BottomBarHint = { key: 'alt+/', description: 'help', align: 'right' }; // width 10
    // The last line's left flow plus a gap plus the right cluster overflows `avail`, so the right
    // hint stacks onto a fresh line rather than overlapping the left hints under space-between.
    const lines = packHints([h('aa', 'bbbb'), right], 9);
    expect(lines).toHaveLength(2);
    expect(lines[0]).not.toContain(right);
    expect(lines.at(-1)).toEqual([right]);
  });

  it('a lone right-aligned hint gets its own line when there are no left hints', () => {
    const right: BottomBarHint = { key: 'alt+/', description: 'help', align: 'right' };
    expect(packHints([right], 80)).toEqual([[right]]);
  });

  it('each produced line fits the width (except a lone oversized hint)', () => {
    const hints = [
      h('alt+1–0', 'panels'),
      h('alt+hjkl', 'nav'),
      h('alt+space', 'chat'),
      h('j', 'next crow'),
      h('k', 'prev crow'),
      h('r', 'refresh'),
      h('m', 'toggle maximized'),
      h('s', 'star'),
    ];
    const avail = 60;
    for (const line of packHints(hints, avail)) {
      const used = line.reduce((sum, hint) => sum + w(hint), 0) + 1 * (line.length - 1);
      if (line.length > 1) {
        expect(used).toBeLessThanOrEqual(avail);
      }
    }
  });
});
