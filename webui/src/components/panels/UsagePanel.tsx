/**
 * UsagePanel — per-harness usage gauges over `usage` via {@link selectUsageView}. Steering Select
 * and meter fill width/`isHigh` map straight from the view; visuals in `panels-usage.css`.
 * Keyboard (when focused): j/k move gauges, r sample.
 */

import { selectUsageView } from '@murder/ui-core/selectors/usageSelectors.js';
import { useAppStore } from '@murder/ui-core/hooks/useAppStore.js';
import { shallow } from 'zustand/shallow';
import { useMemo, useState } from 'react';
import { panelFocusStore, useIsPanelFocused } from '../../panelFocus.js';
import { usePanelListKeys } from '../../usePanelListKeys.js';
import { Panel, Select, cx } from '../ds/index.js';
import { SliceHint } from '../SliceHint.js';

const STEERING_OPTIONS = ['auto', 'pause', 'prefer'] as const;

export function UsagePanel(): React.JSX.Element {
  const usage = useAppStore((s) => s.usage, shallow);
  const setSteering = useAppStore((s) => s.actions.usage.setSteering);
  const sample = useAppStore((s) => s.actions.usage.sample);
  const view = selectUsageView(usage);
  const focused = useIsPanelFocused('usage');
  const [cursor, setCursor] = useState(0);

  const flatGauges = useMemo(
    () =>
      view.groups.flatMap((group) =>
        group.gauges.map((g) => ({ harness: group.harness, windowKey: g.windowKey })),
      ),
    [view.groups],
  );

  usePanelListKeys({
    active: focused,
    itemCount: flatGauges.length,
    cursor,
    setCursor,
    onAction: (key) => {
      if (key === 'r') {
        void sample();
        return true;
      }
      return false;
    },
  });

  let gaugeIndex = 0;

  return (
    <Panel
      title="usage"
      count={view.isEmpty ? null : view.groups.length}
      active={focused}
      data-panel-id="usage"
      onHeaderClick={() => panelFocusStore.getState().focus('usage')}
    >
      <SliceHint state={view} empty="No usage data." />
      {view.groups.map((group) => (
        <div key={group.harness} className="usage-grp">
          <div className="usage-grp__head">
            <span className="usage-grp__harness">{group.harness}</span>
            <Select
              className="usage-grp__steering"
              value={group.steering}
              onChange={(e) => void setSteering(group.harness, e.target.value)}
              title="Scheduler steering"
              options={[...STEERING_OPTIONS]}
            />
          </div>
          {group.gauges.map((g) => {
            const index = gaugeIndex;
            gaugeIndex += 1;
            const cursorHere = focused && index === cursor;
            return (
              <div
                key={g.windowKey}
                className={cx('usage-row', cursorHere && 'usage-row--cursor')}
                onClick={() => {
                  panelFocusStore.getState().focus('usage');
                  setCursor(index);
                }}
              >
                <span
                  className="usage-row__period"
                  title={`${g.windowLabel}${g.periodLabel ? ` ${g.periodLabel}` : ''}`}
                >
                  {g.windowLabel}
                  {g.periodLabel ? ` ${g.periodLabel}` : ''}
                </span>
                <div className="usage-meter" role="progressbar">
                  <i
                    className={cx('usage-meter__fill', g.isHigh && 'usage-meter__fill--high')}
                    style={{ width: `${(g.filledCount / Math.max(1, g.barWidth)) * 100}%` }}
                  />
                </div>
                <span className="usage-row__pct">{g.pctLabel}</span>
                <span className="usage-row__reset" title="time until reset">
                  {g.resetLabel}
                </span>
              </div>
            );
          })}
        </div>
      ))}
    </Panel>
  );
}
