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
import type { UsageTier } from '../layout/budget.js';
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

/**
 * The compact bar width the `mini` tier paints (R9). The full bar is {@link USAGE_BAR_WIDTH}=12; mini
 * shrinks it so the whole line — marker(1) + space(1) + bar — fits the INNER width even at the smallest
 * right rail. A crows+usage rail can compress to `MIN_PANEL_WIDTH=12`, whose Pane inner width is
 * `12 − USAGE_PANE_CHROME(4) = 8`; so the mini line must be ≤ 8: 1 + 1 + 6 = 8 (L4d — at the old 8-cell
 * bar the 10-cell line clipped with `…` in the 8-cell inner space). The geometry is rescaled to this
 * width (see {@link renderBar}) so the fill + marker stay proportionally correct in the shorter bar.
 */
const MINI_BAR_WIDTH = 6;

type UsageIntent = 'cursorDown' | 'cursorUp' | 'refresh';

/**
 * Paint the gauge bar from its geometry at the requested `width`: filled `█` (green / red when high),
 * empty `░` (dim), and a grey `│` marker at the time-through-period position. The selector emits the
 * geometry against the full {@link USAGE_BAR_WIDTH}; when a tier asks for a shorter bar (mini) we
 * RESCALE the filled count + marker cell proportionally so the bar reads correctly at any width.
 * Built as runs of same-style cells so each contiguous segment is a single `<Text>` (the marker is its
 * own one-char span). Returns inline `<Text>` nodes for the parent `<Text>` row.
 */
function renderBar(g: UsageGaugeView, width: number = g.barWidth): JSX.Element {
  const filledColor = g.isHigh ? 'red' : 'green';
  // Rescale the geometry from the selector's full-width bar to the requested width (mini shrinks it).
  const scale = width / g.barWidth;
  const filledCount = Math.round(g.filledCount * scale);
  const markerPos =
    g.markerPos === null ? null : Math.min(width - 1, Math.floor(g.markerPos * scale));
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
  for (let i = 0; i < width; i++) {
    if (i === markerPos) {
      flush(`r${i}`);
      nodes.push(
        <Text key={`m${i}`} color="gray">
          │
        </Text>,
      );
      continue;
    }
    const style = i < filledCount ? 'filled' : 'empty';
    if (style !== runStyle && run.length > 0) flush(`r${i}`);
    runStyle = style;
    run += style === 'filled' ? '█' : '░';
  }
  flush('end');
  return <>{nodes}</>;
}

/** A provider header line: bold harness name on a solid dark, full-width background. Mini drops the
 * solid background (too heavy for a thin column) and shows a dim, lower-cased lead-in instead. */
function HeaderLine({
  harness,
  tier,
}: {
  readonly harness: string;
  readonly tier: UsageTier;
}): JSX.Element {
  if (tier === 'mini') {
    // Mini: no solid band (it crowds a thin column); a dim caret + name reads as a quiet group label.
    return (
      <Box flexShrink={0} width="100%">
        <Text dimColor wrap="truncate">{`· ${harness}`}</Text>
      </Box>
    );
  }
  return (
    <Box flexShrink={0} width="100%" backgroundColor={HEADER_BG}>
      <Text bold wrap="truncate">{` ${harness}`}</Text>
    </Box>
  );
}

/**
 * One gauge line, rendered for the active {@link UsageTier} (R9):
 *  - `mini`   — cursor marker + a compact ({@link MINI_BAR_WIDTH}) bar only; no labels (the bar IS the
 *               signal at the smallest legible width).
 *  - `medium` — marker + full bar + percentage; the window/reset trail is dropped.
 *  - `large`  — marker + full bar + pct + window-length + reset countdown (the complete render).
 * Transparent background unless selected (a subtle highlight). Each tier is laid out deliberately —
 * aligned columns, fixed-width labels — so it reads as a designed variant, not a truncation.
 */
function GaugeLine({
  gauge,
  selected,
  tier,
}: {
  readonly gauge: UsageGaugeView;
  readonly selected: boolean;
  readonly tier: UsageTier;
}): JSX.Element {
  const marker = selected ? '▌' : ' ';
  if (tier === 'mini') {
    return (
      <Box flexShrink={0} width="100%" backgroundColor={selected ? SELECTED_BG : undefined}>
        <Text wrap="truncate">
          {marker} {renderBar(gauge, MINI_BAR_WIDTH)}
        </Text>
      </Box>
    );
  }
  if (tier === 'medium') {
    return (
      <Box flexShrink={0} width="100%" backgroundColor={selected ? SELECTED_BG : undefined}>
        <Text wrap="truncate">
          {marker} {renderBar(gauge)}
          {'  '}
          <Text color={gauge.isHigh ? 'red' : 'white'}>{gauge.pctLabel.padStart(4)}</Text>
        </Text>
      </Box>
    );
  }
  // large — the full render: bar + pct + window-length + reset countdown.
  return (
    <Box flexShrink={0} width="100%" backgroundColor={selected ? SELECTED_BG : undefined}>
      <Text wrap="truncate">
        {marker} {renderBar(gauge)}
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
 * A dim key line labeling the gauge columns (bug 1), rendered per tier so the labels always sit over
 * the columns the tier actually shows: `large` labels usage/pct/window/reset; `medium` labels just
 * usage/pct; `mini` shows no key line at all (the compact bars carry no labels to title). Aligned to
 * {@link GaugeLine}'s layout — 2-col leading gutter (marker + space), the bar width, then the same
 * column widths/spacing — so each label sits over its data. `flexShrink={0}` so the tight right rail
 * doesn't sample it away.
 */
function UsageKeyLine({ tier }: { readonly tier: UsageTier }): JSX.Element | null {
  if (tier === 'mini') {
    return null;
  }
  if (tier === 'medium') {
    return (
      <Box flexShrink={0} width="100%">
        <Text dimColor wrap="truncate">
          {'  '}
          {'usage'.padEnd(USAGE_BAR_WIDTH)}
          {'  '}
          {'pct'.padStart(4)}
        </Text>
      </Box>
    );
  }
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
 * A single running index across all gauges drives the cursor highlight. The {@link UsageTier} chooses
 * the gauge/header/key-line variant (R9). */
function UsageBody({
  view,
  cursor,
  focused,
  tier,
}: {
  readonly view: UsageView;
  readonly cursor: number;
  readonly focused: boolean;
  readonly tier: UsageTier;
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
      <UsageKeyLine tier={tier} />
      {view.groups.map((group) => (
        <Box key={group.harness} flexDirection="column" flexShrink={0}>
          <HeaderLine harness={group.harness} tier={tier} />
          {group.gauges.map((gauge) => {
            gaugeIndex += 1;
            return (
              <GaugeLine
                key={`${group.harness}-${gauge.windowKey}`}
                gauge={gauge}
                selected={focused && gaugeIndex === cursor}
                tier={tier}
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
 *
 * The `tier` (R9, L4) is threaded from the budget engine via App's `renderPanel`: it is the largest
 * gauge variant the right-rail width allots usage (`mini`|`medium`|`large`). The panel forwards it to
 * {@link UsageBody}, which picks the gauge/header/key-line render for that width — the responsive
 * "mini/medium/large version depending on available screen real estate" the spec asks for.
 */
export const UsagePanel = memo(function UsagePanel({
  tier = 'large',
}: {
  /** The gauge variant for the current right-rail width (R9). Defaults to `large` when mounted bare
   * (e.g. a test rendering UsagePanel outside the Rail) so the full render is the unguarded default. */
  readonly tier?: UsageTier;
}): JSX.Element {
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
        tier={tier}
      />
    </Pane>
  );
});
