import { memo, useMemo } from 'react';
import { shallow } from 'zustand/shallow';
import { useAppStore } from '../../hooks/useAppStore.js';
import { useBindings, usePanelKeymap } from '../../hooks/useInputStores.js';
import type { PanelKeymap } from '../../input/keymap.js';
import type { PanePresentation } from '../../layout/paneLayoutTypes.js';
import { selectUsageView } from '../../selectors/usageSelectors.js';
import type { UsageState } from '../../store/usage/usageSlice.js';
import { useTheme } from '../../theme/themeStore.js';
import { AllocatedPaneFrame } from './shared/AllocatedPaneFrame.js';
import { useClampedCursor } from './shared/useClampedCursor.js';
import { UsageSurface, type UsageSurfaceGroup } from './UsageSurface.js';

type UsageIntent = 'cursorDown' | 'cursorUp' | 'refresh' | 'cycleSteering';

const USAGE_STEERING_CYCLE: Record<string, string> = {
  auto: 'prefer',
  prefer: 'pause',
  pause: 'auto',
};

function nextUsageSteering(current: string): string {
  return USAGE_STEERING_CYCLE[current] ?? 'prefer';
}

function pctFromLabel(label: string): number {
  const pct = Number.parseInt(label.replace(/%$/, ''), 10);
  return Number.isFinite(pct) ? pct : 0;
}

export function usageSurfaceGroupsFromState(state: UsageState): readonly UsageSurfaceGroup[] {
  return selectUsageView(state).groups.map((group) => ({
    harness: group.harness,
    steering: group.steering,
    ...(group.fetchedAtLabel === undefined ? {} : { fetchedAt: group.fetchedAtLabel }),
    gauges: group.gauges.map((gauge) => ({
      label: gauge.windowLabel,
      pct: pctFromLabel(gauge.pctLabel),
      reset: gauge.resetLabel,
    })),
  }));
}

function usageGaugeCount(groups: readonly UsageSurfaceGroup[]): number {
  return groups.reduce((count, group) => count + group.gauges.length, 0);
}

function usageSurfaceStatus(status: UsageState['status']): 'ready' | 'loading' | 'error' {
  return status === 'idle' ? 'ready' : status;
}

export interface UsageControllerProps {
  readonly presentation: PanePresentation;
}

export const UsageController = memo(function UsageController({
  presentation,
}: UsageControllerProps): React.JSX.Element {
  const usage = useAppStore((state) => state.usage, shallow);
  const sample = useAppStore((state) => state.actions.usage.sample);
  const setSteering = useAppStore((state) => state.actions.usage.setSteering);
  const bindings = useBindings();
  const theme = useTheme();
  const groups = useMemo(() => usageSurfaceGroupsFromState(usage), [usage]);
  const gaugeCount = usageGaugeCount(groups);
  const { cursor, moveDown, moveUp } = useClampedCursor(gaugeCount);

  const keymap: PanelKeymap<UsageIntent> = useMemo(
    () => ({
      keymap: [
        {
          chord: [{ input: 'j' }, { key: { downArrow: true } }],
          intent: 'cursorDown',
          description: 'next gauge',
        },
        {
          chord: [{ input: 'k' }, { key: { upArrow: true } }],
          intent: 'cursorUp',
          description: 'prev gauge',
        },
        { chord: { input: 'r' }, intent: 'refresh', description: 'sample' },
        {
          chord: bindings.chordsFor('panel.usageSteering'),
          intent: 'cycleSteering',
          description: 'steering',
        },
      ],
      onIntent(intent) {
        switch (intent) {
          case 'cursorDown':
            moveDown();
            return;
          case 'cursorUp':
            moveUp();
            return;
          case 'refresh':
            void sample();
            return;
          case 'cycleSteering': {
            if (gaugeCount === 0) {
              return;
            }
            let index = cursor;
            for (const group of groups) {
              if (index < group.gauges.length) {
                void setSteering(group.harness, nextUsageSteering(group.steering));
                return;
              }
              index -= group.gauges.length;
            }
            return;
          }
          default:
            return intent satisfies never;
        }
      },
    }),
    [bindings, cursor, gaugeCount, groups, moveDown, moveUp, sample, setSteering],
  );
  usePanelKeymap('usage', keymap);

  return (
    <AllocatedPaneFrame id="usage" presentation={presentation}>
      <UsageSurface
        width={presentation.width}
        height={presentation.height}
        focused={presentation.focused}
        theme={theme}
        groups={groups}
        cursor={cursor}
        status={usageSurfaceStatus(usage.status)}
        error={usage.error}
      />
    </AllocatedPaneFrame>
  );
});
