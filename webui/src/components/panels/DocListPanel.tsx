/**
 * DocListPanel — the shared list body for the three doc-backed slices (plans / notes / reports).
 * They have the SAME row shape (name · charCount · updatedAt · starred) and the SAME interactions
 * (click → open the doc in the Stage via `docView.open`; ★ → `favorites.toggle`). Plans add an
 * indent depth (parent/child) and a "spawn planner" affordance. Per the shared-abstraction rule,
 * the three panels are thin wrappers over this one body — never forked.
 *
 * Reskinned onto the design system (Phase C2), following the TicketsPanel exemplar: the DS
 * {@link Panel} container (titled, flush) → one {@link ListRow} per doc. Data wiring is UNCHANGED —
 * same `useAppStore` selectors/actions (`docView.open`, `favorites.toggle`) and the same `rowExtra`
 * mechanism. Mapping onto the DS:
 *  - the pin → ListRow `starred` + `onPinToggle` (replaces the old `.star` button);
 *  - the name → ListRow `title`;
 *  - charCount + updatedAt → ListRow `meta` (terse, lowercase, spacing/muted color, NO middot);
 *  - selection (the open doc) → ListRow `selected`;
 *  - the plans `depth` indent stays a data-driven inline `paddingLeft` (structural, not thematic);
 *  - `rowExtra` (plans' "spawn planner") → ListRow `trailing`.
 */

import type { DocKind } from '@core/store/docView/docViewSlice.js';
import { useAppStore } from '@core/hooks/useAppStore.js';
import { Panel, ListRow } from '../ds/index.js';
import { SliceHint } from '../SliceHint.js';
import type { SliceLike } from '../SliceHint.js';

export interface DocListRow {
  readonly id: string;
  readonly name: string;
  readonly charCount: string;
  readonly updatedAt: string;
  readonly starred: boolean;
  /** Indent depth (plans use 0/1 for parent/child); other slices pass 0. */
  readonly depth?: number;
}

export function DocListPanel({
  title,
  kind,
  rows,
  view,
  empty,
  rowExtra,
}: {
  readonly title: string;
  readonly kind: DocKind;
  readonly rows: readonly DocListRow[];
  readonly view: SliceLike;
  readonly empty: string;
  /** Optional trailing per-row controls (e.g. plans' "spawn planner" button). */
  readonly rowExtra?: (row: DocListRow) => React.ReactNode;
}): React.JSX.Element {
  const openDoc = useAppStore((s) => s.actions.docView.open);
  const toggleFavorite = useAppStore((s) => s.actions.favorites.toggle);
  const openName = useAppStore((s) => (s.docView.open?.kind === kind ? s.docView.open.name : null));

  return (
    <Panel title={title} flush>
      <SliceHint state={view} empty={empty} />
      {rows.map((row) => {
        const extra = rowExtra?.(row);
        return (
          <ListRow
            key={row.id}
            as="button"
            starred={row.starred}
            onPinToggle={() => void toggleFavorite(row.id)}
            selected={row.name.trim() === openName}
            onClick={() => void openDoc(kind, row.id)}
            title={row.name.trim()}
            meta={
              <span className="doc-meta">
                <span className="doc-meta__cell">{row.charCount}</span>
                <span className="doc-meta__cell doc-meta__cell--dim">{row.updatedAt}</span>
              </span>
            }
            trailing={extra ?? undefined}
            style={
              row.depth
                ? { paddingLeft: `calc(var(--space-3) + ${row.depth} * var(--space-4))` }
                : undefined
            }
          />
        );
      })}
    </Panel>
  );
}
