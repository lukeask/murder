/** Grapheme helpers retain UTF-16 offsets, matching JavaScript's string APIs. */
const segmenter: Intl.Segmenter | null =
  typeof Intl !== 'undefined' && typeof Intl.Segmenter === 'function'
    ? new Intl.Segmenter(undefined, { granularity: 'grapheme' })
    : null;

function fallbackNext(text: string, offset: number): number {
  const hi = text.charCodeAt(offset);
  const lo = text.charCodeAt(offset + 1);
  return offset + (hi >= 0xd800 && hi <= 0xdbff && lo >= 0xdc00 && lo <= 0xdfff ? 2 : 1);
}

/** All legal plain-text cursor boundaries, including 0 and text.length. */
export function graphemeBoundaries(text: string): readonly number[] {
  if (text.length === 0) return [0];
  if (segmenter === null) {
    const boundaries = [0];
    for (let i = 0; i < text.length; i = fallbackNext(text, i)) boundaries.push(i);
    return boundaries;
  }
  const boundaries: number[] = [];
  for (const item of segmenter.segment(text)) boundaries.push(item.index);
  boundaries.push(text.length);
  return boundaries;
}

export function previousGraphemeBoundary(text: string, cursor: number): number {
  const boundaries = graphemeBoundaries(text);
  for (let i = boundaries.length - 1; i >= 0; i--) {
    const boundary = boundaries[i] ?? 0;
    if (boundary < cursor) return boundary;
  }
  return 0;
}

export function nextGraphemeBoundary(text: string, cursor: number): number {
  for (const boundary of graphemeBoundaries(text)) if (boundary > cursor) return boundary;
  return text.length;
}

export function normalizeGraphemeCursor(
  text: string,
  cursor: number,
  bias: 'backward' | 'forward' | 'nearest',
): number {
  const clamped = Math.max(0, Math.min(text.length, Math.trunc(cursor)));
  const boundaries = graphemeBoundaries(text);
  let before = 0;
  let after = text.length;
  for (const boundary of boundaries) {
    if (boundary === clamped) return boundary;
    if (boundary < clamped) before = boundary;
    if (boundary > clamped) {
      after = boundary;
      break;
    }
  }
  if (bias === 'backward') return before;
  if (bias === 'forward') return after;
  return clamped - before <= after - clamped ? before : after;
}
