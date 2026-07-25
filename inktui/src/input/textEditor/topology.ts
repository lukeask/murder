import {
  nextGraphemeBoundary,
  normalizeGraphemeCursor,
  previousGraphemeBoundary,
} from './graphemes.js';
import type { TextEditorState } from './state.js';

/** A topology can make source ranges atomic (chat image spans are the current example). */
export interface SpecializedEdit {
  readonly text: string;
  readonly cursor: number;
  readonly removedAtomId?: string;
}

export interface TextTopology {
  previousBoundary(text: string, cursor: number): number;
  nextBoundary(text: string, cursor: number): number;
  normalizeCursor(text: string, cursor: number, bias: 'backward' | 'forward' | 'nearest'): number;
  deleteBefore?(state: TextEditorState): SpecializedEdit | null;
  deleteAt?(state: TextEditorState): SpecializedEdit | null;
}

export const plainTextTopology: TextTopology = {
  previousBoundary: previousGraphemeBoundary,
  nextBoundary: nextGraphemeBoundary,
  normalizeCursor: normalizeGraphemeCursor,
};
