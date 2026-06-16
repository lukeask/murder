/**
 * PlansPanel — plans list (parent/child indent + star + open-doc) over the `plans` + `favorites`
 * slices via {@link selectPlansView}. Adds a "spawn planner" affordance per plan
 * (`plans.spawnPlanner`). A thin wrapper over {@link DocListPanel}.
 */

import { selectPlansView } from '@core/selectors/plansSelectors.js';
import { useAppStore } from '@core/hooks/useAppStore.js';
import { shallow } from 'zustand/shallow';
import { Button } from '../ds/index.js';
import { DocListPanel } from './DocListPanel.js';

export function PlansPanel(): React.JSX.Element {
  const plans = useAppStore((s) => s.plans, shallow);
  const favorites = useAppStore((s) => s.favorites, shallow);
  const spawnPlanner = useAppStore((s) => s.actions.plans.spawnPlanner);
  const view = selectPlansView(plans, favorites);

  return (
    <DocListPanel
      title="Plans"
      kind="plan"
      view={view}
      empty="No plans."
      rows={view.rows.map((r) => ({
        id: r.id,
        name: r.name,
        charCount: r.charCount,
        updatedAt: r.updatedAt,
        starred: r.starred,
        depth: r.depth,
      }))}
      rowExtra={(row) => (
        <Button
          variant="ghost"
          size="sm"
          className="doc-rowaction"
          title="Spawn planner"
          onClick={(e) => {
            e.stopPropagation();
            void spawnPlanner(row.id);
          }}
        >
          plan
        </Button>
      )}
    />
  );
}
