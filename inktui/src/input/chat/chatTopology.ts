import {
  nextGraphemeBoundary,
  normalizeGraphemeCursor,
  previousGraphemeBoundary,
} from '../textEditor/graphemes.js';
import type { TextEditorState } from '../textEditor/state.js';
import type { SpecializedEdit, TextTopology } from '../textEditor/topology.js';
import { scanChatSpans, type ChatSpan } from './chatSpans.js';

function containing(spans: readonly ChatSpan[], offset: number): ChatSpan | undefined {
  return spans.find((span) => offset > span.start && offset < span.end);
}

function ending(spans: readonly ChatSpan[], offset: number): ChatSpan | undefined {
  return spans.find((span) => offset === span.end);
}

function starting(spans: readonly ChatSpan[], offset: number): ChatSpan | undefined {
  return spans.find((span) => offset === span.start);
}

function normalized(
  text: string,
  cursor: number,
  bias: 'backward' | 'forward' | 'nearest',
): number {
  const grapheme = normalizeGraphemeCursor(text, cursor, bias);
  const span = containing(scanChatSpans(text), grapheme);
  if (span === undefined) return grapheme;
  return bias === 'forward' ? span.end : span.start;
}

function removal(text: string, start: number, end: number, id: string): SpecializedEdit {
  return { text: text.slice(0, start) + text.slice(end), cursor: start, removedAtomId: id };
}

export const chatTopology: TextTopology = {
  previousBoundary(text, cursor) {
    const normalizedCursor = normalized(text, cursor, 'backward');
    return (
      ending(scanChatSpans(text), normalizedCursor)?.start ??
      previousGraphemeBoundary(text, normalizedCursor)
    );
  },
  nextBoundary(text, cursor) {
    const normalizedCursor = normalized(text, cursor, 'forward');
    return (
      starting(scanChatSpans(text), normalizedCursor)?.end ??
      nextGraphemeBoundary(text, normalizedCursor)
    );
  },
  normalizeCursor: normalized,
  deleteBefore(state: TextEditorState): SpecializedEdit | null {
    if (state.cursor <= 0) return null;
    const span = ending(scanChatSpans(state.text), state.cursor);
    return span === undefined ? null : removal(state.text, span.start, span.end, span.id);
  },
  deleteAt(state: TextEditorState): SpecializedEdit | null {
    if (state.cursor >= state.text.length) return null;
    const span = starting(scanChatSpans(state.text), state.cursor);
    return span === undefined ? null : removal(state.text, span.start, span.end, span.id);
  },
};

export function snapChatCursor(text: string, offset: number): number {
  return chatTopology.normalizeCursor(text, offset, 'backward');
}
