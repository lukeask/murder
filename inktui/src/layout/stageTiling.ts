/**
 * `stageTiling` — the pure geometry of how the Stage arranges documents and chat-history panes.
 *
 * The Stage holds two kinds of thing: **documents** (left-aligned in landscape / top in portrait) and
 * **chat histories** (right-aligned in landscape / bottom in portrait). How they split the Stage —
 * and how the chats tile among themselves — is a pure function of *how many* of each there are and the
 * terminal {@link Orientation}. Isolating that decision here (no React, no Ink) keeps the rule
 * unit-testable and lets the pane bridge render the result without layout logic in pane components.
 *
 * ## The intuitions this encodes (landscape, one document)
 *  - **1 doc + 1 chat** — side by side, equal halves.
 *  - **1 doc + 2 chats** — doc takes the left half; the two chats stack vertically in the right half.
 *  - **1 doc + 3 chats** — same split; three chats stacked vertically on the right.
 *  - **1 doc + 4 chats** — doc shrinks to the left third; the four chats form a 2×2 grid on the right.
 *
 * Generalised: the chat region stacks in a single column until it holds four panes, at which point it
 * goes two-wide (a roughly-square grid). The document keeps half the width until the chat grid needs
 * the extra room (≥ 4 chats), where it yields to a third so the grid cells stay legible.
 *
 * ## Portrait
 * A tall, narrow Stage can't afford side-by-side columns, so portrait stacks **everything** in one
 * column: the document on top, then the chats one above another (`columns === 1` always). The region
 * weights still apply, now to height rather than width.
 *
 * ## Why a grid instead of one flex row
 * The old Stage tiled every chat pane in a single flex row, so N chats became N skinny columns — four
 * favourited crows left each pane ~a quarter of the Stage wide and illegible. Worse, the panes used
 * the default `auto` flex-basis, so a pane's share depended on its *content* width and on the order
 * panes mounted — opening panes in a different order produced different (often skinny) widths. The
 * renderer pairs this grid with a `flexBasis: 0` discipline on every cell so each cell's size is purely
 * weight-driven and order-independent.
 */

import type { Orientation } from '../hooks/useOrientation.js';

/**
 * How many columns the chat grid uses for `count` chat panes, given whether a document shares the
 * Stage and the {@link Orientation}.
 *  - 0/1 chat → a single column (nothing to tile).
 *  - portrait → always one column (stack vertically; the Stage is too narrow for side-by-side).
 *  - landscape WITH a document → the chats live in the right region (~half width), so they stack in
 *    one column until there are four, then go two-wide (the 2×2 grid).
 *  - landscape WITHOUT a document → the chats own the full Stage width, so two can sit side by side;
 *    three to six use two columns, and seven-plus use three (the cap that keeps a cell readable).
 */
export function chatGridColumns(count: number, hasDoc: boolean, orientation: Orientation): number {
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

/**
 * The flex weights of the document region and the chat region when both are present. The document
 * holds half the Stage (weight 1 : 1) until the chat grid widens to two columns (≥ 4 chats), where the
 * document yields to a third (weight 1 : 2) so the grid cells keep their width. A region with no
 * members gets weight 0 (the renderer omits it entirely; the other region fills the Stage on its own).
 */
export function regionWeights(
  chatCount: number,
  hasDoc: boolean,
): { readonly doc: number; readonly chat: number } {
  return {
    doc: hasDoc ? 1 : 0,
    chat: chatCount === 0 ? 0 : chatCount >= 4 ? 2 : 1,
  };
}

/** Split `items` into rows of at most `columns` entries, left to right, top to bottom. The last row
 * may be short. `columns < 1` is treated as 1 (a single column) so the result is always well-formed. */
export function chunkRows<T>(items: readonly T[], columns: number): T[][] {
  const cols = Math.max(columns, 1);
  const rows: T[][] = [];
  for (let i = 0; i < items.length; i += cols) {
    rows.push(items.slice(i, i + cols));
  }
  return rows;
}

/** The fully-resolved Stage arrangement for a set of chat panes plus an optional document. */
export interface StageLayout<T> {
  /** Flex weight for the document region (0 when no document is open). */
  readonly docWeight: number;
  /** Flex weight for the chat region (0 when there are no chat panes). */
  readonly chatWeight: number;
  /** The chat panes grouped into grid rows (each rendered as a cross-axis line). Empty when none. */
  readonly rows: readonly T[][];
}

/**
 * Resolve the complete Stage arrangement: the region weights plus the chat panes chunked into grid
 * rows. The pane bridge renders the doc region (when `docWeight > 0`) and one cross-axis line per
 * `rows` entry, both with a `flexBasis: 0` cell discipline so sizing is weight-driven and
 * order-independent.
 */
export function computeStageLayout<T>(
  chats: readonly T[],
  hasDoc: boolean,
  orientation: Orientation,
): StageLayout<T> {
  const columns = chatGridColumns(chats.length, hasDoc, orientation);
  const { doc, chat } = regionWeights(chats.length, hasDoc);
  return { docWeight: doc, chatWeight: chat, rows: chunkRows(chats, columns) };
}
