/**
 * ReportsPanel — reports list over the `reports` + `favorites` slices via {@link selectReportsView}.
 * A thin wrapper over {@link DocListPanel}.
 */

import { selectReportsView } from '@core/selectors/reportsSelectors.js';
import { useAppStore } from '@core/hooks/useAppStore.js';
import { shallow } from 'zustand/shallow';
import { DocListPanel } from './DocListPanel.js';

export function ReportsPanel(): React.JSX.Element {
  const reports = useAppStore((s) => s.reports, shallow);
  const favorites = useAppStore((s) => s.favorites, shallow);
  const view = selectReportsView(reports, favorites);

  return (
    <DocListPanel
      title="Reports"
      kind="report"
      view={view}
      empty="No reports."
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
