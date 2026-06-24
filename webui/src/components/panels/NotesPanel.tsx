/**
 * NotesPanel — notes list over the `notes` + `favorites` slices via {@link selectNotesView}.
 * A thin wrapper over {@link DocListPanel}.
 */

import { selectNotesView } from '@core/selectors/notesSelectors.js';
import { useAppStore } from '@core/hooks/useAppStore.js';
import { shallow } from 'zustand/shallow';
import { DocListPanel } from './DocListPanel.js';

export function NotesPanel(): React.JSX.Element {
  const notes = useAppStore((s) => s.notes, shallow);
  const favorites = useAppStore((s) => s.favorites, shallow);
  const view = selectNotesView(notes, favorites);

  return (
    <DocListPanel
      title="notes"
      kind="note"
      view={view}
      empty="No notes."
      rows={view.rows.map((r) => ({
        id: r.name,
        name: r.name,
        charCount: r.charCount,
        updatedAt: r.updatedAt,
        starred: r.starred,
      }))}
    />
  );
}
