/**
 * Stage transcript tiling — count-only helpers mirroring TUI `inktui/src/layout/stageTiling.ts`.
 * Pure geometry for WebUI CSS grid; no inktui imports.
 *
 * Landscape intuitions (one document):
 *  - 1 doc + 1–3 transcripts → doc half; transcripts stack in one column on the right.
 *  - 1 doc + ≥4 transcripts → doc third; transcripts 2-wide.
 * Without a doc: 2 side-by-side, then 2 cols to 6, 3 cols beyond.
 * Portrait / narrow: always stack (1 column).
 */

export type StageOrientation = 'landscape' | 'portrait';

/** How many columns the transcript grid uses for `count` panes. */
export function transcriptGridColumns(
  count: number,
  hasDoc: boolean,
  orientation: StageOrientation,
): number {
  if (count <= 1) {
    return 1;
  }
  if (orientation === 'portrait') {
    return 1;
  }
  if (hasDoc) {
    return count <= 3 ? 1 : 2;
  }
  if (count <= 2) {
    return count;
  }
  return count <= 6 ? 2 : 3;
}

/** Flex weights for doc vs transcript regions when both present. */
export function regionWeights(
  transcriptCount: number,
  hasDoc: boolean,
): { readonly doc: number; readonly transcript: number } {
  return {
    doc: hasDoc ? 1 : 0,
    transcript: transcriptCount === 0 ? 0 : transcriptCount >= 4 ? 2 : 1,
  };
}

/** Split `items` into rows of at most `columns` (last row may be short). */
export function chunkRows<T>(items: readonly T[], columns: number): T[][] {
  const cols = Math.max(columns, 1);
  const rows: T[][] = [];
  for (let i = 0; i < items.length; i += cols) {
    rows.push(items.slice(i, i + cols));
  }
  return rows;
}

export interface StageLayout<T> {
  readonly docWeight: number;
  readonly transcriptWeight: number;
  readonly columns: number;
  readonly rows: readonly T[][];
}

/** Resolve region weights + transcript panes chunked into grid rows. */
export function computeStageLayout<T>(
  transcripts: readonly T[],
  hasDoc: boolean,
  orientation: StageOrientation,
): StageLayout<T> {
  const columns = transcriptGridColumns(transcripts.length, hasDoc, orientation);
  const { doc, transcript } = regionWeights(transcripts.length, hasDoc);
  return {
    docWeight: doc,
    transcriptWeight: transcript,
    columns,
    rows: chunkRows(transcripts, columns),
  };
}
