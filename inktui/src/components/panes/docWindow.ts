export function computeDocWindow(
  total: number,
  scroll: number,
  height: number,
): { start: number; end: number; maxScroll: number } {
  const h = Math.max(height, 1);
  const maxScroll = Math.max(total - h, 0);
  const start = Math.min(Math.max(scroll, 0), maxScroll);
  return { start, end: start + h, maxScroll };
}

export function computeScrollThumb(
  total: number,
  scroll: number,
  height: number,
): { size: number; offset: number } | null {
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
