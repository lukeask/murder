/**
 * UsagePanel — the usage right-rail panel (panel 9, C9).
 *
 * ## Grouped, not a uniform list (why no Ledger here)
 * Phase 3 converted every list panel to {@link ./Pane.tsx Pane} + {@link ./Ledger.tsx Ledger}, but
 * usage is no longer a flat uniform-row list: it renders 2–3 lines per provider — a dark **header**
 * line (the harness name) then one transparent **gauge** line per rate-limit window. That grouped
 * shape with semantic per-line backgrounds fights the Ledger's uniform-row + alternating-parity
 * model, so this panel keeps the Pane chrome but renders its grouped body directly. The provider set
 * is fixed (claude_code / codex / cursor × a couple of windows ≈ ≤9 lines), so the Ledger's overflow
 * windowing isn't needed; the Pane clips if the share is tight.
 *
 * ## The gauge bar
 * The selector emits each bar as *geometry* (`filledCount` + `markerPos`), not a finished string, so
 * this component paints the segments: filled cells `█` (green, or red when `isHigh`), empty cells `░`
 * (dim), and a grey `│` overlaid at `markerPos` to show how far through the *time* window we are
 * (e.g. 6 days into a 7-day window → marker near the right, independent of how much quota is used).
 *
 * The panel is `React.memo`'d (rule 1), owns a local cursor over the flat gauge list, declares its
 * keymap (rule 5), and reaches the bus only through the dispatched `actions.usage.refresh` (rule 3).
 */

import { Box, Text } from 'ink';
import { type JSX, memo, useMemo, useState } from 'react';
import { shallow } from 'zustand/shallow';
import { useAppStore } from '../hooks/useAppStore.js';
import {
  useEffectiveFocus,
  useFocusRef,
  useMeasureFocus,
  usePanelKeymap,
} from '../hooks/useInputStores.js';
import type { PanelKeymap } from '../input/keymap.js';
import type { PanelId } from '../input/panels.js';
import {
  USAGE_BAR_WIDTH,
  type UsageGaugeView,
  type UsageView,
  useUsageView,
} from '../selectors/usageSelectors.js';
import { Pane } from './Pane.js';

const PANEL_ID: PanelId = 'usage';
const PANEL_TITLE = 'Usage';

/** Solid dark background for a provider header line (the "dark on provider" request). */
const HEADER_BG = '#181825';
/** Subtle highlight background for the cursor-selected gauge line (gauges are otherwise transparent). */
const SELECTED_BG = '#313244';

type UsageIntent = 'cursorDown' | 'cursorUp' | 'refresh';

/**
 * Paint the gauge bar from its geometry: filled `█` (green / red when high), empty `░` (dim), and a
 * grey `│` marker at `markerPos`. Built as runs of same-style cells so each contiguous segment is a
 * single `<Text>` (the marker is its own one-char span). Returns inline `<Text>` nodes for the parent
 * `<Text>` row.
 */
function renderBar(g: UsageGaugeView): JSX.Element {
  const filledColor = g.isHigh ? 'red' : 'green';
  const nodes: JSX.Element[] = [];
  let run = '';
  let runStyle: 'filled' | 'empty' = 'filled';
  const flush = (key: string): void => {
    if (run.length === 0) return;
    nodes.push(
      runStyle === 'filled' ? (
        <Text key={key} color={filledColor}>
          {run}
        </Text>
      ) : (
        <Text key={key} dimColor>
          {run}
        </Text>
      ),
    );
    run = '';
  };
  for (let i = 0; i < g.barWidth; i++) {
    if (i === g.markerPos) {
      flush(`r${i}`);
      nodes.push(
        <Text key={`m${i}`} color="gray">
          │
        </Text>,
      );
      continue;
    }
    const style = i < g.filledCount ? 'filled' : 'empty';
    if (style !== runStyle && run.length > 0) flush(`r${i}`);
    runStyle = style;
    run += style === 'filled' ? '█' : '░';
  }
  flush('end');
  return <>{nodes}</>;
}

/** A provider header line: bold harness name on a solid dark, full-width background. */
function HeaderLine({ harness }: { readonly harness: string }): JSX.Element {
  return (
    <Box flexShrink={0} width="100%" backgroundColor={HEADER_BG}>
      <Text bold wrap="truncate">{` ${harness}`}</Text>
    </Box>
  );
}

/** One gauge line: cursor marker, segmented bar, pct, window-length, and reset countdown. Transparent
 * background unless selected (a subtle highlight). */
function GaugeLine({
  gauge,
  selected,
}: {
  readonly gauge: UsageGaugeView;
  readonly selected: boolean;
}): JSX.Element {
  return (
    <Box flexShrink={0} width="100%" backgroundColor={selected ? SELECTED_BG : undefined}>
      <Text wrap="truncate">
        {selected ? '▌' : ' '} {renderBar(gauge)}
        {'  '}
        <Text color={gauge.isHigh ? 'red' : 'white'}>{gauge.pctLabel.padStart(4)}</Text>
        {'  '}
        <Text dimColor>{gauge.periodLabel.padEnd(3)}</Text>{' '}
        <Text dimColor>{gauge.resetLabel.padStart(7)}</Text>
      </Text>
    </Box>
  );
}

/**
 * A dim key line labeling the gauge columns (bug 1): `usage` over the bar, then `pct`, `window`,
 * `reset`. Aligned to {@link GaugeLine}'s layout — 2-col leading gutter (marker + space), the bar
 * width, then the same `pct`/`period`/`reset` column widths/spacing — so each label sits over its
 * data. Rendered once at the top of the body, above the provider blocks. `flexShrink={0}` so the tight
 * right rail doesn't sample it away.
 */
function UsageKeyLine(): JSX.Element {
  return (
    <Box flexShrink={0} width="100%">
      <Text dimColor wrap="truncate">
        {'  '}
        {'usage'.padEnd(USAGE_BAR_WIDTH)}
        {'  '}
        {'pct'.padStart(4)}
        {'  '}
        {'win'.padEnd(3)} {'reset'.padStart(7)}
      </Text>
    </Box>
  );
}

/** The list body: loading/error/empty chrome, else one block per provider (header + gauge lines).
 * A single running index across all gauges drives the cursor highlight. */
function UsageBody({
  view,
  cursor,
  focused,
}: {
  readonly view: UsageView;
  readonly cursor: number;
  readonly focused: boolean;
}): JSX.Element {
  if (view.status === 'error') {
    return <Text color="red">{`error: ${view.error ?? 'unknown'}`}</Text>;
  }
  if (view.status === 'loading' && view.isEmpty) {
    return <Text dimColor>loading…</Text>;
  }
  if (view.isEmpty) {
    return <Text dimColor>no usage data</Text>;
  }
  let gaugeIndex = -1;
  return (
    <Box flexDirection="column" flexShrink={0}>
      <UsageKeyLine />
      {view.groups.map((group) => (
        <Box key={group.harness} flexDirection="column" flexShrink={0}>
          <HeaderLine harness={group.harness} />
          {group.gauges.map((gauge) => {
            gaugeIndex += 1;
            return (
              <GaugeLine
                key={`${group.harness}-${gauge.windowKey}`}
                gauge={gauge}
                selected={focused && gaugeIndex === cursor}
              />
            );
          })}
        </Box>
      ))}
    </Box>
  );
}

/** Total gauge count across all provider groups (the flat cursor range). */
function countGauges(view: UsageView): number {
  return view.groups.reduce((n, g) => n + g.gauges.length, 0);
}

/**
 * The usage panel. Reads the usage slice, runs the selector to grouped display-ready data, owns a
 * local cursor over the flat gauge list, declares its keymap, and paints a focus-highlighted Pane of
 * provider blocks. `React.memo`'d (rule 1) so it re-renders only when its own state changes.
 */
export const UsagePanel = memo(function UsagePanel(): JSX.Element {
  // Rule 1: narrow selector (shallow). Rule 2: selector produces display-ready groups.
  const usage = useAppStore((s) => s.usage, shallow);
  const view = useUsageView(usage);
  // Rule 3: bus reached only through the dispatched action.
  const refresh = useAppStore((s) => s.actions.usage.refresh);

  // Local UI state: cursor over the flat gauge list (rule 1).
  const [cursor, setCursor] = useState(0);
  const gaugeCount = countGauges(view);

  // Rule 5: keymap as data in useMemo.
  const keymap: PanelKeymap<UsageIntent> = useMemo(
    () => ({
      keymap: [
        { chord: { input: 'j' }, intent: 'cursorDown', description: 'next gauge' },
        { chord: { input: 'k' }, intent: 'cursorUp', description: 'prev gauge' },
        { chord: { input: 'r' }, intent: 'refresh', description: 'refresh' },
      ],
      onIntent(intent) {
        switch (intent) {
          case 'cursorDown':
            setCursor((c) => (gaugeCount === 0 ? 0 : Math.min(c + 1, gaugeCount - 1)));
            return;
          case 'cursorUp':
            setCursor((c) => Math.max(c - 1, 0));
            return;
          case 'refresh':
            void refresh();
            return;
          default:
            return intent satisfies never;
        }
      },
    }),
    [gaugeCount, refresh],
  );
  usePanelKeymap(PANEL_ID, keymap);

  // Focus highlight + rect registration — identical across every panel (rule 5).
  const ref = useFocusRef();
  const focused = useEffectiveFocus() === PANEL_ID;
  useMeasureFocus(PANEL_ID, ref);

  return (
    <Pane ref={ref} title={PANEL_TITLE} focused={focused}>
      <UsageBody
        view={view}
        cursor={Math.min(cursor, Math.max(gaugeCount - 1, 0))}
        focused={focused}
      />
    </Pane>
  );
});
