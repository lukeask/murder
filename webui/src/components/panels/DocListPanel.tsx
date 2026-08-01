/**
 * DocListPanel — shared list body for plans / notes / reports. Same row shape and interactions
 * (open via `docView.open`, ★ via `favorites.toggle`); plans add depth indent + optional `rowExtra`.
 */

import type { DocKind } from '@murder/ui-core/store/docView/docViewSlice.js';
import { useAppStore } from '@murder/ui-core/hooks/useAppStore.js';
import { Panel, ListRow } from '../ds/index.js';
import { SliceHint } from '../SliceHint.js';
import type { SliceLike } from '../SliceHint.js';

export interface DocListRow {
  readonly id?: string;
  readonly name: string;
  readonly charCount: string;
  readonly updatedAt: string;
  readonly starred: boolean;
  /** Indent depth (plans use 0/1 for parent/child); other slices omit. */
  readonly depth?: number;
}

export function DocListPanel({
  title,
  kind,
  rows,
  view,
  empty,
  rowExtra,
  actions,
}: {
  readonly title: string;
  readonly kind: DocKind;
  readonly rows: readonly DocListRow[];
  readonly view: SliceLike;
  readonly empty: string;
  /** Optional trailing per-row controls (e.g. plans' "spawn planner" button). */
  readonly rowExtra?: (row: DocListRow) => React.ReactNode;
  /** Optional header actions (e.g. new-plan "+"). */
  readonly actions?: React.ReactNode;
}): React.JSX.Element {
  const openDoc = useAppStore((s) => s.actions.docView.open);
  const toggleFavorite = useAppStore((s) => s.actions.favorites.toggle);
  const openName = useAppStore((s) => (s.docView.open?.kind === kind ? s.docView.open.name : null));

  return (
    <Panel
      title={title}
      flush
      actions={actions}
      data-panel-id={kind === 'plan' ? 'plans' : kind === 'note' ? 'notes' : 'reports'}
    >
      <SliceHint state={view} empty={empty} />
      {rows.map((row) => {
        const id = row.id ?? row.name;
        const extra = rowExtra?.(row);
        return (
          <ListRow
            key={id}
            starred={row.starred}
            onPinToggle={() => void toggleFavorite(id)}
            selected={row.name.trim() === openName}
            onClick={() => void openDoc(kind, id)}
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
