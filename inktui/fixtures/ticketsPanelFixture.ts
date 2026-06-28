import type { TicketsSurfaceRow } from '../src/components/panes/TicketsSurface.js';
import type { TicketFixtureRow } from './data/paneFixtureData.js';

/** Map fixture rows to display-ready TicketsSurface rows. */
export function ticketFixtureToSurfaceRows(
  rows: readonly TicketFixtureRow[],
): readonly TicketsSurfaceRow[] {
  return rows.map((row) => ({
    id: row.id,
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
