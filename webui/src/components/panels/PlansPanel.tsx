/**
 * PlansPanel — plans list (parent/child indent + star + open-doc) over plans + favorites.
 * Adds a per-plan "spawn planner" affordance; thin DocListPanel wrapper.
 */

import { selectPlansView } from '@murder/ui-core/selectors/plansSelectors.js';
import { useAppStore } from '@murder/ui-core/hooks/useAppStore.js';
import { shallow } from 'zustand/shallow';
import { useCreationDialogs } from '../../creationDialogs.js';
import { Button, IconButton, Icon } from '../ds/index.js';
import { DocListPanel } from './DocListPanel.js';

export function PlansPanel(): React.JSX.Element {
  const plans = useAppStore((s) => s.plans, shallow);
  const favorites = useAppStore((s) => s.favorites, shallow);
  const spawnPlanner = useAppStore((s) => s.actions.plans.spawnPlanner);
  const { openPlan } = useCreationDialogs();
  const view = selectPlansView(plans, favorites);

  return (
    <DocListPanel
      title="plans"
      kind="plan"
      view={view}
      empty="No plans."
      actions={
        <IconButton label="New plan" onClick={openPlan}>
          <Icon name="plus" size={14} />
        </IconButton>
      }
      rows={view.rows}
      rowExtra={(row) => (
        <Button
          variant="ghost"
          size="sm"
          className="doc-rowaction"
          title="Spawn planner"
          onClick={(e) => {
            e.stopPropagation();
            void spawnPlanner(row.id ?? row.name);
          }}
        >
          plan
        </Button>
      )}
    />
  );
}
