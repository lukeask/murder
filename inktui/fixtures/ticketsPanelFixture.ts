import type { TicketsPanelRow } from '../src/components/panes/TicketsPanel.js';
import type { TicketFixtureRow } from './data/paneFixtureData.js';

/** Map fixture rows to display-ready TicketsPanel rows (Phase 0 baseline). */
export function ticketFixtureToPanelRows(
  rows: readonly TicketFixtureRow[],
): readonly TicketsPanelRow[] {
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
