/**
 * UsagePanel — per-harness usage gauges over the `usage` slice via {@link selectUsageView}. Each
 * group shows its windows as labelled progress bars (CSS-drawn from `filledCount`/`barWidth`) and a
 * steering selector (`usage.setSteering`). The selector pre-formats all labels (rule 2); the
 * component only draws the bar fill ratio and maps `isHigh` → a CSS class.
 */

import { selectUsageView } from '@core/selectors/usageSelectors.js';
import { useAppStore } from '@core/hooks/useAppStore.js';
import { shallow } from 'zustand/shallow';
import { Panel } from '../Panel.js';
import { SliceHint } from '../SliceHint.js';

const STEERING_OPTIONS = ['auto', 'pause', 'prefer'] as const;

export function UsagePanel(): React.JSX.Element {
  const usage = useAppStore((s) => s.usage, shallow);
  const setSteering = useAppStore((s) => s.actions.usage.setSteering);
  const view = selectUsageView(usage);

  return (
    <Panel title="Usage">
      <SliceHint state={view} empty="No usage data." />
      {view.groups.map((group) => (
        <div key={group.harness} className="usage__group">
          <div className="usage__head">
            <span className="usage__harness">{group.harness}</span>
            <select
              className="usage__steering"
              value={group.steering}
              onChange={(e) => void setSteering(group.harness, e.target.value)}
              title="Scheduler steering"
            >
              {STEERING_OPTIONS.map((opt) => (
                <option key={opt} value={opt}>
                  {opt}
                </option>
              ))}
            </select>
          </div>
          {group.gauges.map((g) => (
            <div key={g.windowKey} className="gauge">
              <span className="gauge__period">{g.periodLabel}</span>
              <div className="gauge__bar" role="progressbar">
                <div
                  className={g.isHigh ? 'gauge__fill gauge__fill--high' : 'gauge__fill'}
                  style={{ width: `${(g.filledCount / Math.max(1, g.barWidth)) * 100}%` }}
                />
                <span className="gauge__pct">{g.pctLabel}</span>
              </div>
              <span className="gauge__reset" title="time until reset">
                {g.resetLabel}
              </span>
            </div>
          ))}
        </div>
      ))}
    </Panel>
  );
}
