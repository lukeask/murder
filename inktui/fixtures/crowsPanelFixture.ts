import type { CrowsPanelRow } from '../src/components/panes/CrowsPanel.js';
import { classifyCrowHealth } from '../src/selectors/crowHealthSelectors.js';
import type { CrowFixtureRow } from './data/paneFixtureData.js';

/** Map fixture rows into the store-free CrowsPanel row shape. */
export function crowsPanelRowsFromFixture(
  rows: readonly CrowFixtureRow[],
): readonly CrowsPanelRow[] {
  return rows.map((row) => ({
    id: row.id,
    group: row.group,
    name: row.starred ? row.name.replace(/^★\s*/, '') : row.name,
    meta: row.meta,
    working: row.working,
    starred: row.starred,
    health: classifyCrowHealth({ status: row.status }),
  }));
}
