/**
 * UsageBarSegment — compact `usage <harness> <reset>` ledger snippet from
 * {@link selectUsageBarWidget}. Renders in NavBar trailing or KeybindBar (not a floating chip).
 */

import { useAppStore } from '@murder/ui-core/hooks/useAppStore.js';
import { resolveBarWidgetConfig } from '@murder/ui-core/selectors/barWidgetRegistry.js';
import { selectUsageBarWidget } from '@murder/ui-core/selectors/selectUsageBarWidget.js';
import { useMemo } from 'react';
import { shallow } from 'zustand/shallow';
import { cx } from './ds/cx.js';

export interface UsageBarSegmentProps {
  readonly className?: string;
  /** When set, only render if the usage widget is enabled for this placement. */
  readonly placement?: 'top' | 'bottom';
}

/** Live usage-reset timer segment for the nav beam / keybind ledger. */
export function UsageBarSegment({
  className,
  placement,
}: UsageBarSegmentProps): React.JSX.Element | null {
  const usage = useAppStore((s) => s.usage, shallow);
  const barWidgets = useAppStore((s) => s.settings.barWidgets);
  const config = resolveBarWidgetConfig('usage', barWidgets);

  const segment = useMemo(
    () => selectUsageBarWidget(usage.rows, config.harnesses),
    [usage.rows, config.harnesses],
  );

  if (!config.enabled) {
    return null;
  }
  if (placement !== undefined && config.placement !== placement) {
    return null;
  }
  if (segment === null) {
    return null;
  }

  return (
    <span className={cx('cockpit__usage-seg', className)} aria-label="usage reset timer">
      {segment.runs.map((run, i) => (
        <span
          key={i}
          className={cx(
            'cockpit__usage-seg__run',
            run.style.dim === true && 'cockpit__usage-seg__run--muted',
          )}
        >
          {run.text}
        </span>
      ))}
    </span>
  );
}
