/**
 * NotesPanel / ReportsPanel — thin DocListPanel wrappers over notes/reports + favorites.
 * Differ only by slice selector, title, kind, and empty copy.
 */

import { selectNotesView } from '@core/selectors/notesSelectors.js';
import { selectReportsView } from '@core/selectors/reportsSelectors.js';
import type { FavoritesState } from '@core/store/favorites/favoritesSlice.js';
import type { DocKind } from '@core/store/docView/docViewSlice.js';
import { useAppStore } from '@core/hooks/useAppStore.js';
import type { AppStore } from '@core/store/store.js';
import { shallow } from 'zustand/shallow';
import { DocListPanel } from './DocListPanel.js';
import type { DocListRow } from './DocListPanel.js';
import type { SliceLike } from '../SliceHint.js';

function makeDocSlicePanel<TSlice>(opts: {
  readonly title: string;
  readonly kind: Extract<DocKind, 'note' | 'report'>;
  readonly empty: string;
  readonly selectSlice: (s: AppStore) => TSlice;
  readonly selectView: (
    slice: TSlice,
    favorites: FavoritesState,
  ) => SliceLike & { readonly rows: readonly DocListRow[] };
}): () => React.JSX.Element {
  const { title, kind, empty, selectSlice, selectView } = opts;
  return function DocSlicePanel(): React.JSX.Element {
    const slice = useAppStore(selectSlice, shallow);
    const favorites = useAppStore((s) => s.favorites, shallow);
    const view = selectView(slice, favorites);
    return <DocListPanel title={title} kind={kind} view={view} empty={empty} rows={view.rows} />;
  };
}

export const NotesPanel = makeDocSlicePanel({
  title: 'notes',
  kind: 'note',
  empty: 'No notes.',
  selectSlice: (s) => s.notes,
  selectView: selectNotesView,
});

export const ReportsPanel = makeDocSlicePanel({
  title: 'reports',
  kind: 'report',
  empty: 'No reports.',
  selectSlice: (s) => s.reports,
  selectView: selectReportsView,
});
