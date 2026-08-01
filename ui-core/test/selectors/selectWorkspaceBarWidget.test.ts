/**
 * selectWorkspaceBarWidget — fixed-width workspace tab strip for the bar widget.
 */

import { describe, expect, it } from 'vitest';
import { selectWorkspaceBarWidget } from '@murder/ui-core/selectors/selectWorkspaceBarWidget.js';

function segmentText(
  segment: ReturnType<typeof selectWorkspaceBarWidget>,
): string | undefined {
  return segment?.runs.map((run) => run.text).join('');
}

const COLORS = { activeBg: '#green', activeFg: '#ink', inactiveFg: '#muted' };

describe('selectWorkspaceBarWidget', () => {
  it('three workspaces → padded number tabs with the active index highlighted', () => {
    const segment = selectWorkspaceBarWidget(1, 3, COLORS);
    expect(segmentText(segment)).toBe(' 1  2  3 ');
    expect(segment?.runs).toEqual([
      { text: ' 1 ', style: { fg: '#muted' } },
      { text: ' 2 ', style: { bold: true, bg: '#green', fg: '#ink' } },
      { text: ' 3 ', style: { fg: '#muted' } },
    ]);
  });

  it('four workspaces → one tab per workspace', () => {
    const segment = selectWorkspaceBarWidget(0, 4, COLORS);
    expect(segmentText(segment)).toBe(' 1  2  3  4 ');
    expect(segment?.runs).toHaveLength(4);
    expect(segment?.width).toBe(12);
  });

  it('collapses when count is one', () => {
    expect(selectWorkspaceBarWidget(0, 1, COLORS)).toBeNull();
  });

  it('first workspace is shown as 1', () => {
    const segment = selectWorkspaceBarWidget(0, 2, COLORS);
    expect(segmentText(segment)).toBe(' 1  2 ');
    expect(segment?.runs[0]?.style).toEqual({ bold: true, bg: '#green', fg: '#ink' });
    expect(segment?.runs[1]?.style).toEqual({ fg: '#muted' });
  });

  it('emits a tab for every configured workspace up to nine', () => {
    const segment = selectWorkspaceBarWidget(8, 9, COLORS);
    expect(segment?.runs).toHaveLength(9);
    expect(segmentText(segment)).toBe(' 1  2  3  4  5  6  7  8  9 ');
    expect(segment?.runs[8]?.style).toMatchObject({ bold: true, bg: '#green' });
  });

  it('segment width is three cells per workspace', () => {
    const segment = selectWorkspaceBarWidget(2, 5, COLORS);
    expect(segment?.width).toBe(15);
    expect(segment?.width).toBe(segmentText(segment)?.length);
  });

  it('without colors the active tab is bold-only', () => {
    const segment = selectWorkspaceBarWidget(0, 2);
    expect(segment?.runs[0]?.style).toEqual({ bold: true });
  });
});
