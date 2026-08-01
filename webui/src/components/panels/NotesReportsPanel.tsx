/**
 * NotesPanel / ReportsPanel — thin DocListPanel wrappers over notes/reports + favorites.
 * Differ only by slice selector, title, kind, empty copy, and create-dialog opener.
 */

import { selectNotesView } from '@murder/ui-core/selectors/notesSelectors.js';
import { selectReportsView } from '@murder/ui-core/selectors/reportsSelectors.js';
import { useAppStore } from '@murder/ui-core/hooks/useAppStore.js';
import { shallow } from 'zustand/shallow';
import { useCreationDialogs } from '../../creationDialogs.js';
import { IconButton, Icon } from '../ds/index.js';
import { DocListPanel } from './DocListPanel.js';

export function NotesPanel(): React.JSX.Element {
  const notes = useAppStore((s) => s.notes, shallow);
  const favorites = useAppStore((s) => s.favorites, shallow);
  const view = selectNotesView(notes, favorites);
  const { openNoteCapture } = useCreationDialogs();
  return (
    <DocListPanel
      title="notes"
      kind="note"
      view={view}
      empty="No notes."
      rows={view.rows}
      actions={
        <IconButton label="New note" onClick={openNoteCapture}>
          <Icon name="plus" size={14} />
        </IconButton>
      }
    />
  );
}

export function ReportsPanel(): React.JSX.Element {
  const reports = useAppStore((s) => s.reports, shallow);
  const favorites = useAppStore((s) => s.favorites, shallow);
  const view = selectReportsView(reports, favorites);
  const { openReport } = useCreationDialogs();
  return (
    <DocListPanel
      title="reports"
      kind="report"
      view={view}
      empty="No reports."
      rows={view.rows}
      actions={
        <IconButton label="New report" onClick={openReport}>
          <Icon name="plus" size={14} />
        </IconButton>
      }
    />
  );
}
