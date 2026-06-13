/**
 * DocListPanel — the shared list body for the three doc-backed slices (plans / notes / reports).
 * They have the SAME row shape (name · charCount · updatedAt · starred) and the SAME interactions
 * (click → open the doc in the Stage via `docView.open`; ★ → `favorites.toggle`). Plans add an
 * indent depth (parent/child) and a "spawn planner" affordance. Per the shared-abstraction rule,
 * the three panels are thin wrappers over this one body — never forked.
 */

import type { DocKind } from '@core/store/docView/docViewSlice.js';
import { useAppStore } from '@core/hooks/useAppStore.js';
import { Panel } from '../Panel.js';
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
    <Panel title={title}>
      <SliceHint state={view} empty={empty} />
      <ul className="list">
        {rows.map((row) => (
          <li
            key={row.id}
            className="list__row doc__row"
            data-selected={row.name.trim() === openName ? 'true' : undefined}
            style={row.depth ? { paddingLeft: `calc(var(--space-3) + ${row.depth} * var(--space-4))` } : undefined}
            onClick={() => void openDoc(kind, row.id)}
          >
            <button
              type="button"
              className="star"
              aria-pressed={row.starred}
              title={row.starred ? 'Unstar' : 'Star'}
              onClick={(e) => {
                e.stopPropagation();
                void toggleFavorite(row.id);
              }}
            >
              {row.starred ? '★' : '☆'}
            </button>
            <span className="list__primary doc__name">{row.name.trim()}</span>
            <span className="doc__meta">{row.charCount}</span>
            <span className="doc__meta doc__meta--dim">{row.updatedAt}</span>
            {rowExtra?.(row)}
          </li>
        ))}
      </ul>
    </Panel>
  );
}
