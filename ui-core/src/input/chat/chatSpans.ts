/** Invisible source markers for an image draft embedded in chat text. */
export const SPAN_OPEN = '\u{E000}';
export const SPAN_CLOSE = '\u{E001}';
export const SPAN_RE = new RegExp(`${SPAN_OPEN}([^${SPAN_OPEN}${SPAN_CLOSE}]*)${SPAN_CLOSE}`, 'g');

export interface ChatSpan {
  readonly id: string;
  readonly start: number;
  readonly end: number;
}

export function makeSpan(id: string): string {
  return `${SPAN_OPEN}${id}${SPAN_CLOSE}`;
}

export function scanChatSpans(text: string): readonly ChatSpan[] {
  const spans: ChatSpan[] = [];
  for (const match of text.matchAll(SPAN_RE)) {
    const start = match.index ?? 0;
    spans.push({ id: match[1] ?? '', start, end: start + match[0].length });
  }
  return spans;
}

export function spanLabels(
  text: string,
): readonly { readonly id: string; readonly label: string }[] {
  return scanChatSpans(text).map((span, index) => ({ id: span.id, label: `[Image ${index + 1}]` }));
}
