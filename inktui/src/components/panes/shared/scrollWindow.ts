export interface DocumentWindow {
  readonly start: number;
  readonly end: number;
  readonly maxScroll: number;
}

export interface TranscriptWindow {
  readonly start: number;
  readonly end: number;
  readonly maxScrollUp: number;
  readonly clampedScrollUp: number;
}

export interface ScrollThumb {
  readonly size: number;
  readonly offset: number;
}

export function computeDocumentWindow(
  total: number,
  scroll: number,
  height: number,
): DocumentWindow {
  const h = Math.max(height, 1);
  const maxScroll = Math.max(total - h, 0);
  const start = Math.min(Math.max(scroll, 0), maxScroll);
  return { start, end: start + h, maxScroll };
}

export function computeTranscriptWindow(
  total: number,
  scrollUp: number,
  height: number,
  gotoLine: number | null = null,
): TranscriptWindow {
  const h = Math.max(height, 1);
  const maxScrollUp = Math.max(total - h, 0);
  const requestedScroll =
    gotoLine === null ? scrollUp : Math.min(Math.max(maxScrollUp - (gotoLine - 1), 0), maxScrollUp);
  const clampedScrollUp = Math.min(Math.max(requestedScroll, 0), maxScrollUp);
  const end = total - clampedScrollUp;
  return {
    start: Math.max(end - h, 0),
    end,
    maxScrollUp,
    clampedScrollUp,
  };
}

export function computeScrollThumb(
  total: number,
  scroll: number,
  height: number,
): ScrollThumb | null {
  const h = Math.max(height, 1);
  if (total <= h) {
    return null;
  }
  const maxScroll = total - h;
  const size = Math.max(1, Math.round((h * h) / total));
  const clampedScroll = Math.min(Math.max(scroll, 0), maxScroll);
  const offset = maxScroll > 0 ? Math.round((clampedScroll / maxScroll) * (h - size)) : 0;
  return { size, offset: Math.min(offset, h - size) };
}
