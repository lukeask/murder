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
 * The selector emits each bar as *geometry* (`filledCount` over `barWidth`), not a finished string,
 * so this component paints the segments: filled cells `█` (green, or red when `isHigh`), the unused
 * remainder `░` in the grey track color, and the pct label EMBEDDED in the bar — over the fill (its
 * background = the fill color) when the fill is wide enough, else right-aligned on the grey track.
 *
 * ## Fluid width (R9)
 * The gauge line sizes off the INNER width the budget engine grants (threaded in as `innerWidth`):
 * the bar greedily absorbs the width left after the win+reset trail, and when that would squeeze the
 * bar under {@link MIN_GAUGE_BAR_WIDTH} the line sheds first the window-length label, then the reset
 * countdown — the bar itself is the last thing standing.
 *
 * The panel is `React.memo`'d (rule 1), owns a local cursor over the flat gauge list, declares its
 * keymap (rule 5), and reaches the bus only through the dispatched `actions.usage.refresh` (rule 3).
 */

import { Box, Text } from 'ink';
import { type JSX, memo, useMemo, useState } from 'react';
import { shallow } from 'zustand/shallow';
import { useAppStore } from '../hooks/useAppStore.js';
import {
  useBindings,
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
import type { Theme } from '../theme/buildTheme.js';
import { useTheme } from '../theme/themeStore.js';
import { Pane } from './Pane.js';

const PANEL_ID: PanelId = 'usage';
const PANEL_TITLE = 'Usage';

/** Cursor-marker gutter every gauge/key line leads with: marker(1) + space(1). */
const GUTTER_WIDTH = 2;
/** Gap between the bar and its win/reset trail (and between the key line's column labels). */
const TRAIL_GAP = 2;
/** Fixed width of the window-length column (`5h`, `30d`, left-aligned). */
const WIN_WIDTH = 3;
/** Fixed width of the reset-countdown column (`1h52m`, right-aligned). */
const RESET_WIDTH = 7;
/**
 * The narrowest bar that still reads as a gauge. The bar absorbs the inner width greedily; when a
 * label trail would squeeze it under this, the line sheds the win label first, then the reset
 * countdown (see {@link gaugeLayoutFor}) — the bar itself is never given up.
 */
const MIN_GAUGE_BAR_WIDTH = 8;
/** Minimum leading filled cells for the pct label to sit ON the fill; under this it right-aligns on
 * the grey track instead (a 1–2 cell fill can't carry a 3-char label legibly). */
const MIN_EMBED_FILL = 3;
/** The inner width assumed when mounted bare (e.g. a test rendering UsagePanel outside the Rail):
 * the full line at the selector's nominal bar width, so the full render is the unguarded default. */
const DEFAULT_INNER_WIDTH =
  GUTTER_WIDTH + USAGE_BAR_WIDTH + TRAIL_GAP + WIN_WIDTH + 1 + RESET_WIDTH;

type UsageIntent = 'cursorDown' | 'cursorUp' | 'refresh' | 'cycleSteering';

/** The steering cycle order applied on each `s` press: auto → prefer → pause → auto. */
const STEERING_CYCLE: Record<string, string> = {
  auto: 'prefer',
  prefer: 'pause',
  pause: 'auto',
};

/** Next steering value in the cycle; unknown values restart at 'prefer' (after 'auto'). */
function nextSteering(current: string): string {
  return STEERING_CYCLE[current] ?? 'prefer';
}

/**
 * What the fluid gauge line shows at a given inner width (R9): the bar's cell count plus which
 * labels survive. Resolution order — full (bar + win + reset), then drop win, then drop reset; the
 * bar greedily takes every cell the surviving trail leaves it, and a trail is dropped exactly when
 * keeping it would squeeze the bar under {@link MIN_GAUGE_BAR_WIDTH}.
 */
interface GaugeLayout {
  readonly barWidth: number;
  readonly showWin: boolean;
  readonly showReset: boolean;
}

/** Resolve the {@link GaugeLayout} for the inner width the budget engine granted. Pure; total — the
 * bar is floored at 1 cell even at degenerate widths. */
function gaugeLayoutFor(innerWidth: number): GaugeLayout {
  const fullBar = innerWidth - GUTTER_WIDTH - TRAIL_GAP - WIN_WIDTH - 1 - RESET_WIDTH;
  if (fullBar >= MIN_GAUGE_BAR_WIDTH) {
    return { barWidth: fullBar, showWin: true, showReset: true };
  }
  const resetBar = innerWidth - GUTTER_WIDTH - TRAIL_GAP - RESET_WIDTH;
  if (resetBar >= MIN_GAUGE_BAR_WIDTH) {
    return { barWidth: resetBar, showWin: false, showReset: true };
  }
  return { barWidth: Math.max(1, innerWidth - GUTTER_WIDTH), showWin: false, showReset: false };
}

/**
 * Paint the gauge bar from its geometry at the requested `width`: filled `█` (green / red when high),
 * the unused remainder `░` in the grey track color, and the pct label EMBEDDED in the bar:
 *  - fill ≥ {@link MIN_EMBED_FILL} cells (and wide enough for the label) → the label leads the bar,
 *    its background the fill color, the rest of the fill solid after it;
 *  - otherwise → the label right-aligns at the bar's end on a grey band matching the track.
 * The selector emits the geometry against the full {@link USAGE_BAR_WIDTH}; we RESCALE the filled
 * count proportionally so the bar reads correctly at any width. Returns inline `<Text>` nodes for
 * the parent `<Text>` row.
 */
function renderBar(g: UsageGaugeView, theme: Theme, width: number = g.barWidth): JSX.Element {
  const filledColor = g.isHigh ? theme.gaugeHigh : theme.gaugeNormal;
  // Rescale the geometry from the selector's full-width bar to the requested width.
  const filledCount = Math.min(width, Math.round((g.filledCount * width) / g.barWidth));
  // Totality at degenerate widths: never let the label exceed the bar itself.
  const label = g.pctLabel.length <= width ? g.pctLabel : g.pctLabel.slice(0, width);
  if (filledCount >= Math.max(MIN_EMBED_FILL, label.length)) {
    // The label sits ON the fill: same background as the solid cells, then the rest of the fill.
    return (
      <>
        <Text color={theme.gaugeLabelText} backgroundColor={filledColor}>
          {label}
        </Text>
        <Text color={filledColor}>{'█'.repeat(filledCount - label.length)}</Text>
        {filledCount < width ? (
          <Text color={theme.gaugeTrack}>{'░'.repeat(width - filledCount)}</Text>
        ) : null}
      </>
    );
  }
  // Thin fill: the label right-aligns on a grey band matching the track; fill + track precede it.
  const shownFilled = Math.min(filledCount, Math.max(0, width - label.length));
  const trackCount = Math.max(0, width - shownFilled - label.length);
  return (
    <>
      {shownFilled > 0 ? <Text color={filledColor}>{'█'.repeat(shownFilled)}</Text> : null}
      {trackCount > 0 ? <Text color={theme.gaugeTrack}>{'░'.repeat(trackCount)}</Text> : null}
      <Text color={theme.gaugeLabelText} backgroundColor={theme.gaugeTrack}>
        {label}
      </Text>
    </>
  );
}

/** A provider header line: bold harness name on a solid dark, full-width background. The bare layout
 * (no labels survive) drops the solid background (too heavy for a thin column) and shows a dim,
 * lower-cased lead-in instead. */
function HeaderLine({
  harness,
  compact,
  steering,
}: {
  readonly harness: string;
  readonly compact: boolean;
  /** RT5 steering for this harness; a ` [paused]`/` [preferred]` tag trails the name (auto: none). */
  readonly steering: string;
}): JSX.Element {
  const theme = useTheme();
  const tag = steering === 'pause' ? ' [paused]' : steering === 'prefer' ? ' [preferred]' : '';
  if (compact) {
    // No solid band (it crowds a thin column); a dim caret + name reads as a quiet group label.
    return (
      <Box flexShrink={0} width="100%">
        <Text dimColor wrap="truncate">
          {`· ${harness}`}
          {tag ? <Text color={theme.accent}>{tag}</Text> : null}
        </Text>
      </Box>
    );
  }
  return (
    <Box flexShrink={0} width="100%" backgroundColor={theme.panelHeaderBg}>
      <Text bold wrap="truncate">
        {` ${harness}`}
        {tag ? <Text color={theme.accent}>{tag}</Text> : null}
      </Text>
    </Box>
  );
}

/**
 * One gauge line at the resolved {@link GaugeLayout}: cursor marker + the bar (pct embedded — see
 * {@link renderBar}) + whichever of the win/reset trail survived the width. Transparent background
 * unless selected (a subtle highlight). Fixed-width trail columns so the gauges align as a table.
 */
function GaugeLine({
  gauge,
  selected,
  layout,
}: {
  readonly gauge: UsageGaugeView;
  readonly selected: boolean;
  readonly layout: GaugeLayout;
}): JSX.Element {
  const theme = useTheme();
  const marker = selected ? '▌' : ' ';
  return (
    <Box flexShrink={0} width="100%" backgroundColor={selected ? theme.panelSelectedBg : undefined}>
      <Text wrap="truncate">
        {marker} {renderBar(gauge, theme, layout.barWidth)}
        {layout.showWin ? (
          <>
            {'  '}
            <Text dimColor>{gauge.periodLabel.padEnd(WIN_WIDTH)}</Text>
          </>
        ) : null}
        {layout.showReset ? (
          <>
            {layout.showWin ? ' ' : '  '}
            <Text dimColor>{gauge.resetLabel.padStart(RESET_WIDTH)}</Text>
          </>
        ) : null}
      </Text>
    </Box>
  );
}

/**
 * A dim key line labeling the gauge columns (bug 1), rendered for the resolved {@link GaugeLayout}
 * so the labels always sit over the columns the width actually shows: `usage` spans the bar (the pct
 * is embedded in it, so it has no column of its own), then `win`/`reset` only when they survived.
 * The bare layout shows no key line at all. Aligned to {@link GaugeLine}'s layout — 2-col leading
 * gutter (marker + space), the live bar width, then the same column widths/spacing — so each label
 * sits over its data. `flexShrink={0}` so the tight right rail doesn't sample it away.
 */
function UsageKeyLine({ layout }: { readonly layout: GaugeLayout }): JSX.Element | null {
  if (!layout.showReset) {
    return null;
  }
  return (
    <Box flexShrink={0} width="100%">
      <Text dimColor wrap="truncate">
        {'  '}
        {'usage'.padEnd(layout.barWidth)}
        {'  '}
        {layout.showWin ? `${'win'.padEnd(WIN_WIDTH)} ` : ''}
        {'reset'.padStart(RESET_WIDTH)}
      </Text>
    </Box>
  );
}

/** The list body: loading/error/empty chrome, else one block per provider (header + gauge lines).
 * A single running index across all gauges drives the cursor highlight. The resolved
 * {@link GaugeLayout} chooses the gauge/header/key-line variant (R9). */
function UsageBody({
  view,
  cursor,
  focused,
  layout,
}: {
  readonly view: UsageView;
  readonly cursor: number;
  readonly focused: boolean;
  readonly layout: GaugeLayout;
}): JSX.Element {
  const theme = useTheme();
  if (view.status === 'error') {
    return <Text color={theme.error}>{`error: ${view.error ?? 'unknown'} (r to retry)`}</Text>;
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
      <UsageKeyLine layout={layout} />
      {view.groups.map((group) => (
        <Box key={group.harness} flexDirection="column" flexShrink={0}>
          <HeaderLine
            harness={group.harness}
            compact={!layout.showReset}
            steering={group.steering}
          />
          {group.gauges.map((gauge) => {
            gaugeIndex += 1;
            return (
              <GaugeLine
                key={`${group.harness}-${gauge.windowKey}`}
                gauge={gauge}
                selected={focused && gaugeIndex === cursor}
                layout={layout}
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
 * The `innerWidth` (R9, L4) is threaded from the budget engine via App's `renderPanel`: the width
 * the gauges actually draw in. The panel resolves it to a {@link GaugeLayout} (greedy bar; the win
 * then reset labels shed as the bar would fall under {@link MIN_GAUGE_BAR_WIDTH}) and forwards that
 * to {@link UsageBody} — the responsive render the spec asks for.
 */
export const UsagePanel = memo(function UsagePanel({
  innerWidth = DEFAULT_INNER_WIDTH,
}: {
  /** The inner width the budget engine grants the gauges (R9). Defaults to the full line at the
   * nominal bar width when mounted bare (e.g. a test rendering UsagePanel outside the Rail) so the
   * full render is the unguarded default. */
  readonly innerWidth?: number;
}): JSX.Element {
  // Rule 1: narrow selector (shallow). Rule 2: selector produces display-ready groups.
  const usage = useAppStore((s) => s.usage, shallow);
  const view = useUsageView(usage);
  // Rule 3: bus reached only through the dispatched actions.
  const refresh = useAppStore((s) => s.actions.usage.refresh);
  const setSteering = useAppStore((s) => s.actions.usage.setSteering);
  // The cycle chord (`s`) comes from the central registry (`panel.usageSteering`); `bindings` is a
  // stable handle, so it's a sound keymap dep.
  const bindings = useBindings();

  // Local UI state: cursor over the flat gauge list (rule 1).
  const [cursor, setCursor] = useState(0);
  const gaugeCount = countGauges(view);
  const clampedCursor = Math.min(cursor, Math.max(gaugeCount - 1, 0));

  // Rule 5: keymap as data in useMemo.
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
        { chord: { input: 'r' }, intent: 'refresh', description: 'refresh' },
        {
          chord: bindings.chordsFor('panel.usageSteering'),
          intent: 'cycleSteering',
          description: 'steering',
        },
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
          case 'cycleSteering': {
            if (gaugeCount === 0) return;
            // Resolve the cursored gauge's harness by walking groups with the same flat index
            // UsageBody uses, then cycle that group's steering and dispatch (bus only via actions).
            let idx = clampedCursor;
            for (const group of view.groups) {
              if (idx < group.gauges.length) {
                void setSteering(group.harness, nextSteering(group.steering));
                return;
              }
              idx -= group.gauges.length;
            }
            return;
          }
          default:
            return intent satisfies never;
        }
      },
    }),
    [gaugeCount, clampedCursor, refresh, setSteering, bindings, view.groups],
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
        cursor={clampedCursor}
        focused={focused}
        layout={gaugeLayoutFor(innerWidth)}
      />
    </Pane>
  );
});
