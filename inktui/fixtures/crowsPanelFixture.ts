import type { CrowsSurfaceRow } from '../src/components/panes/CrowsSurface.js';
import { classifyCrowHealth } from '../src/selectors/crowHealthSelectors.js';
import type { CrowFixtureRow } from './data/paneFixtureData.js';

/** Map fixture rows into the store-free CrowsSurface row shape. */
export function crowsSurfaceRowsFromFixture(
  rows: readonly CrowFixtureRow[],
): readonly CrowsSurfaceRow[] {
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
