import { describe, expect, it } from 'vitest';
import {
  concatFrames,
  displayWidth,
  normalizeTextFrame,
  sliceFrameWindow,
  stripAnsiCsi,
  type TextFrame,
} from '../../src/render/frameText.js';

const RED = '\x1b[31m';
const RESET = '\x1b[0m';

function lineWidths(frame: TextFrame): number[] {
  return frame.text.split('\n').map((line) => displayWidth(line));
}

function frame(lines: readonly string[], columns: number): TextFrame {
  return { text: lines.join('\n'), columns, rows: lines.length };
}

describe('frameText — cookbook', () => {
  it('stripAnsiCsi and displayWidth ignore CSI bytes when measuring columns', () => {
    const line = `${RED}hey${RESET}`;
    expect(stripAnsiCsi(line)).toBe('hey');
    expect(displayWidth(line)).toBe(3);
    expect(displayWidth('ab')).toBe(2);
  });

  it('normalizeTextFrame pads short rows to the declared width and height', () => {
    const normalized = normalizeTextFrame({ text: 'ab\nc', columns: 4, rows: 3 });
    expect(normalized.text).toBe('ab  \nc   \n    ');
    expect(lineWidths(normalized)).toEqual([4, 4, 4]);
  });

  it('concatFrames stacks next as from-then-to and prev as to-then-from', () => {
    const from = frame(['AA', 'BB'], 2);
    const to = frame(['11', '22'], 2);

    const next = concatFrames(from, to, 'next');
    expect(next.rows).toBe(4);
    expect(next.text).toBe('AA\nBB\n11\n22');

    const prev = concatFrames(from, to, 'prev');
    expect(prev.text).toBe('11\n22\nAA\nBB');
  });

  it('sliceFrameWindow returns a fixed-height window at the eased row offset', () => {
    const from = frame(['AA', 'BB'], 2);
    const to = frame(['11', '22'], 2);
    const stacked = concatFrames(from, to, 'next');

    expect(sliceFrameWindow(stacked, 0, 2).text).toBe('AA\nBB');
    expect(sliceFrameWindow(stacked, 1, 2).text).toBe('BB\n11');
    expect(sliceFrameWindow(stacked, 2, 2).text).toBe('11\n22');
  });

  it('prev concat + decreasing offsets slide from → to', () => {
    const from = frame(['AA', 'BB'], 2);
    const to = frame(['11', '22'], 2);
    const stacked = concatFrames(from, to, 'prev');

    expect(sliceFrameWindow(stacked, 2, 2).text).toBe('AA\nBB');
    expect(sliceFrameWindow(stacked, 1, 2).text).toBe('22\nAA');
    expect(sliceFrameWindow(stacked, 0, 2).text).toBe('11\n22');
  });
});

describe('frameText — edge cases', () => {
  it('preserves ANSI styling inside sliced rows', () => {
    const from = frame([`${RED}AA${RESET}`, `${RED}BB${RESET}`], 2);
    const to = frame([`${RED}11${RESET}`, `${RED}22${RESET}`], 2);
    const stacked = concatFrames(from, to, 'next');
    const mid = sliceFrameWindow(stacked, 1, 2);

    expect(mid.text).toBe(`${RED}BB${RESET}\n${RED}11${RESET}`);
    expect(lineWidths(mid)).toEqual([2, 2]);
    expect(mid.text).toContain(RED);
  });

  it('pads when the window extends past the frame bottom', () => {
    const tiny = frame(['x'], 3);
    const window = sliceFrameWindow(tiny, 0, 2);
    expect(window.text).toBe('x  \n   ');
    expect(lineWidths(window)).toEqual([3, 3]);
  });

  it('clamps a negative offset to zero', () => {
    const source = frame(['aa', 'bb', 'cc'], 2);
    expect(sliceFrameWindow(source, -3, 2).text).toBe('aa\nbb');
  });

  it('rejects concat when frame dimensions differ', () => {
    const a = frame(['a'], 2);
    const b = frame(['bb'], 3);
    expect(() => concatFrames(a, b, 'next')).toThrow(/size mismatch/);
  });
});
