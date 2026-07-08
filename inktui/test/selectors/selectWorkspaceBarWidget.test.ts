/**
 * selectWorkspaceBarWidget — active workspace index for the workspace bar widget.
 */

import { describe, expect, it } from 'vitest';
import { selectWorkspaceBarWidget } from '../../src/selectors/selectWorkspaceBarWidget.js';

function segmentText(
  segment: ReturnType<typeof selectWorkspaceBarWidget>,
): string | undefined {
  return segment?.runs.map((run) => run.text).join('');
}

describe('selectWorkspaceBarWidget', () => {
  it('three workspaces → shows one-based active index', () => {
    const segment = selectWorkspaceBarWidget(1, 3);
    expect(segmentText(segment)).toBe('⟨2/3⟩');
  });

  it('collapses when count is one', () => {
    expect(selectWorkspaceBarWidget(0, 1)).toBeNull();
  });

  it('first workspace is shown as 1', () => {
    const segment = selectWorkspaceBarWidget(0, 2);
    expect(segmentText(segment)).toBe('⟨1/2⟩');
  });

  it('last workspace uses the configured count', () => {
    const segment = selectWorkspaceBarWidget(8, 9);
    expect(segmentText(segment)).toBe('⟨9/9⟩');
  });

  it('segment width matches joined run text length', () => {
    const segment = selectWorkspaceBarWidget(2, 5);
    expect(segment?.width).toBe(segmentText(segment)?.length);
  });
});
