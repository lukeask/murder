import { describe, expect, it } from 'vitest';
import {
  computeDocumentWindow,
  computeScrollThumb,
  computeTranscriptWindow,
} from '../../../src/components/panes/shared/scrollWindow.js';

describe('scrollWindow helpers', () => {
  it('clamps document windows to the available range', () => {
    expect(computeDocumentWindow(10, -3, 4)).toEqual({ start: 0, end: 4, maxScroll: 6 });
    expect(computeDocumentWindow(10, 99, 4)).toEqual({ start: 6, end: 10, maxScroll: 6 });
    expect(computeDocumentWindow(2, 5, 4)).toEqual({ start: 0, end: 4, maxScroll: 0 });
  });

  it('maps transcript scroll-up offsets and goto lines into bottom-anchored windows', () => {
    expect(computeTranscriptWindow(12, 0, 4)).toEqual({
      start: 8,
      end: 12,
      maxScrollUp: 8,
      clampedScrollUp: 0,
    });
    expect(computeTranscriptWindow(12, 3, 4)).toEqual({
      start: 5,
      end: 9,
      maxScrollUp: 8,
      clampedScrollUp: 3,
    });
    expect(computeTranscriptWindow(12, 0, 4, 2)).toEqual({
      start: 1,
      end: 5,
      maxScrollUp: 8,
      clampedScrollUp: 7,
    });
  });

  it('returns thumb geometry only when content overflows', () => {
    expect(computeScrollThumb(4, 0, 4)).toBeNull();
    expect(computeScrollThumb(20, 8, 5)).toEqual({ size: 1, offset: 2 });
    expect(computeScrollThumb(20, 99, 5)).toEqual({ size: 1, offset: 4 });
  });
});
