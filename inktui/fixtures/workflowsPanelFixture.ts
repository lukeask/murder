import type { WorkflowsSurfaceRow } from '../src/components/panes/WorkflowsSurface.js';
import type { TicketFixtureRow } from './data/paneFixtureData.js';

/** Map fixture rows to display-ready WorkflowsSurface rows (legacy-style standalone tickets). */
export function workflowFixtureToSurfaceRows(
  rows: readonly TicketFixtureRow[],
): readonly WorkflowsSurfaceRow[] {
  return rows.map((row) => ({
    id: `ticket:${row.id}`,
    kind: 'legacy-ticket-run' as const,
    idCell: row.id,
    titleCell: row.title,
    statusCell: row.status,
    statusTone: row.statusTone,
    lastUpdateCell: 'Jun. 21',
    depsCell: row.deps,
    depsSatisfied: row.depsOk,
    scheduleCell: 'queued',
    harnessCell: row.harness,
    modelCell: row.model,
    planCell: '—',
    worktreeCell: '—',
  }));
}
