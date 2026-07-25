import type { DisplayAtom, DisplayProjection } from './projection.js';
import type { TextEditorState } from './state.js';

export interface VisualRow {
  readonly atoms: readonly DisplayAtom[];
  readonly sourceStart: number;
  readonly sourceEnd: number;
  readonly columns: number;
  /** Boundary-to-column data includes consumed soft-wrap whitespace. */
  readonly boundaries: readonly { readonly sourceOffset: number; readonly column: number }[];
}

export interface VisualLayout {
  readonly rows: readonly VisualRow[];
  readonly cursorRow: number;
  readonly cursorColumn: number;
}

function widthOf(atoms: readonly DisplayAtom[]): number {
  return atoms.reduce((total, atom) => total + atom.columns, 0);
}

function row(
  atoms: readonly DisplayAtom[],
  fallbackOffset: number,
  leadingSeams: readonly number[] = [],
): VisualRow {
  const atomStart = atoms[0]?.sourceStart ?? fallbackOffset;
  const sourceStart = leadingSeams[0] ?? atomStart;
  const sourceEnd = atoms.at(-1)?.sourceEnd ?? fallbackOffset;
  const boundaries: { sourceOffset: number; column: number }[] = leadingSeams.map(
    (sourceOffset) => ({ sourceOffset, column: 0 }),
  );
  if (boundaries.at(-1)?.sourceOffset !== atomStart) {
    boundaries.push({ sourceOffset: atomStart, column: 0 });
  }
  let column = 0;
  for (const atom of atoms) {
    column += atom.columns;
    boundaries.push({ sourceOffset: atom.sourceEnd, column });
  }
  return { atoms, sourceStart, sourceEnd, columns: column, boundaries };
}

function isBreakable(atom: DisplayAtom | undefined): boolean {
  return atom !== undefined && /^\s+$/.test(atom.text) && atom.text !== '\n';
}

/**
 * Compute terminal rows once, for both rendering and vertical movement.  The source coordinates are
 * never rewritten on resize; a caller merely invokes this again with its newly allocated width.
 */
export function layoutEditor(
  state: TextEditorState,
  width: number,
  projection: DisplayProjection,
): VisualLayout {
  const maxWidth = Math.max(1, Math.floor(width));
  const atoms = projection(state.text);
  const rows: VisualRow[] = [];
  let current: DisplayAtom[] = [];
  let fallbackOffset = 0;
  /** Source boundaries of soft-wrapped whitespace, mapped to column zero on the following row. */
  let leadingSeams: number[] = [];

  const pushCurrent = (emptyOffset = fallbackOffset): void => {
    rows.push(row(current, emptyOffset, leadingSeams));
    current = [];
    leadingSeams = [];
  };

  for (const atom of atoms) {
    if (atom.text === '\n') {
      pushCurrent(atom.sourceStart);
      fallbackOffset = atom.sourceEnd;
      continue;
    }
    const currentWidth = widthOf(current);
    if (current.length > 0 && currentWidth + atom.columns > maxWidth) {
      // A whitespace atom that would begin the next row is a soft-wrap seam: it is not painted and
      // the following word starts the new row. Its source offset remains represented by that row's
      // fallback/source start, so legal cursors never disappear.
      if (isBreakable(atom)) {
        pushCurrent();
        leadingSeams = [atom.sourceStart, atom.sourceEnd];
        fallbackOffset = atom.sourceEnd;
        continue;
      }
      let breakAt = -1;
      for (let i = current.length - 1; i >= 0; i--) {
        if (isBreakable(current[i])) {
          breakAt = i;
          break;
        }
      }
      if (breakAt >= 0) {
        let beforeEnd = breakAt;
        while (beforeEnd > 0 && isBreakable(current[beforeEnd - 1])) beforeEnd--;
        const before = current.slice(0, beforeEnd);
        rows.push(row(before, before[0]?.sourceStart ?? fallbackOffset, leadingSeams));
        leadingSeams = current
          .slice(beforeEnd, breakAt + 1)
          .flatMap((candidate) => [candidate.sourceStart, candidate.sourceEnd]);
        // Preserve the whitespace source locations as a zero-column seam in the next row. It is not
        // painted, but cursor mapping at those legal source boundaries remains deterministic.
        current = current.slice(breakAt + 1).filter((candidate) => !isBreakable(candidate));
      } else {
        pushCurrent();
      }
    }
    // A single wide atom cannot be split below grapheme level. It owns a row even when wider than the
    // allocation; this is preferable to corrupting a ZWJ/combining sequence.
    current.push(atom);
    fallbackOffset = atom.sourceEnd;
  }
  if (current.length > 0 || rows.length === 0 || atoms.at(-1)?.text === '\n')
    pushCurrent(fallbackOffset);

  // A cursor exactly after a full final row needs a drawable terminal cell.
  const final = rows.at(-1);
  if (
    final !== undefined &&
    state.cursor === state.text.length &&
    final.columns >= maxWidth &&
    final.sourceEnd === state.cursor
  ) {
    rows.push(row([], state.cursor));
  }

  let cursorRow = 0;
  let cursorColumn = 0;
  for (let i = 0; i < rows.length; i++) {
    const candidate = rows[i];
    if (candidate === undefined) continue;
    const hit = candidate.boundaries.find((boundary) => boundary.sourceOffset === state.cursor);
    if (hit !== undefined) {
      cursorRow = i;
      cursorColumn = hit.column;
      // A soft seam has the same source boundary in two rows: prefer the later row.
      continue;
    }
    if (state.cursor >= candidate.sourceStart && state.cursor <= candidate.sourceEnd) {
      cursorRow = i;
      cursorColumn = candidate.columns;
    }
  }
  return { rows, cursorRow, cursorColumn };
}

export function sourceOffsetAt(row: VisualRow, column: number): number {
  let chosen = row.sourceStart;
  for (const boundary of row.boundaries) {
    if (boundary.column > column) break;
    chosen = boundary.sourceOffset;
  }
  return chosen;
}
