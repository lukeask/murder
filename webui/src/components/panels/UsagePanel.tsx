/**
 * UsagePanel — per-harness usage gauges over the `usage` slice via {@link selectUsageView} (Phase C2
 * reskin onto the design system; follows the TicketsPanel exemplar).
 *
 * Data wiring is UNCHANGED: same `useAppStore(s.usage)` + `selectUsageView(usage)` → groups[]
 * .{harness, steering, gauges[]}, same `usage.setSteering`. Each gauge still drives the meter fill
 * width from `filledCount/barWidth` (data-driven inline width) and maps `isHigh` → a high (red) fill.
 *
 * Presentation moved onto DS primitives + tokens: the DS {@link Panel} container, a DS {@link Select}
 * for steering, and a tokenized meter (track = `--surface-active`/`--ef-bg-4`, fill = `--accent`,
 * high fill = `--text-error`). Visuals live in `styles/panels-usage.css` (wired in via main.tsx),
 * mirroring the design bundle's `.usage-grp`/`.mw-usagebar` (7–8px accent meter, pill radius, row =
 * tool name + pct in accent + window/reset muted). Lifecycle stays {@link SliceHint}.
 */

import { selectUsageView } from '@core/selectors/usageSelectors.js';
import { useAppStore } from '@core/hooks/useAppStore.js';
import { shallow } from 'zustand/shallow';
import { Panel, Select, cx } from '../ds/index.js';
import { SliceHint } from '../SliceHint.js';

const STEERING_OPTIONS = ['auto', 'pause', 'prefer'] as const;

export function UsagePanel(): React.JSX.Element {
  const usage = useAppStore((s) => s.usage, shallow);
  const setSteering = useAppStore((s) => s.actions.usage.setSteering);
  const view = selectUsageView(usage);

  return (
    <Panel title="usage" count={view.isEmpty ? null : view.groups.length} data-panel-id="usage">
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
          {group.gauges.map((g) => (
            <div key={g.windowKey} className="usage-row">
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
          ))}
        </div>
      ))}
    </Panel>
  );
}
