/**
 * UsageSurface — store-free, dimension-driven usage gauges for fixtures and the new pane contract.
 *
 * Grouped provider blocks (header + gauge lines per rate-limit window). Accepts explicit
 * `width`/`height` (full allocation including border, title, footer, padding). A local layout
 * router picks a display mode; gauge line width degrades via {@link gaugeLayoutFor}.
 */

import { Box, Text } from 'ink';
import { Fragment, memo, useMemo } from 'react';
import { USAGE_BAR_WIDTH } from '../../selectors/usageSelectors.js';
import type { Theme } from '../../theme/buildTheme.js';
import { COMPACT_PANE_PADDING_CW, Pane } from '../Pane.js';

const PANEL_TITLE = 'Usage';

/** Vertical chrome: title row + bottom border. */
const CHROME_HEIGHT = 2;
/** Key legend needs a row plus at least one provider block (header + gauge). */
const FULL_KEY_MIN_INNER_H = 9;

/** Comfortable inner width at/above which the pane keeps spaced markers and label gaps. */
const TIGHT_PADDING_MAX_COMFORTABLE_INNER_W = COMPACT_PANE_PADDING_CW;

/** One space between the usage bar and the win/reset trail (never dropped). */
const BAR_TRAIL_GAP = 1;
/** Win column — period labels like `16h` / `6h` (≤3 chars). */
const WIN_WIDTH = 3;
/** Reset column — countdown strings like `1h12m` (≤6 chars), right-aligned in the pane. */
const RESET_WIDTH = 6;
/** Comfortable mid-trail slot: variable gap + reset text; reset right edge lines up across rows. */
const RESET_TRAIL_SLOT = RESET_WIDTH + 2;
/** Tight tier: one space between the win label and reset countdown. */
const TIGHT_WIN_RESET_GAP = 1;
const MIN_GAUGE_BAR_WIDTH = 7;
const MIN_EMBED_FILL = 3;
const EMPTY_MESSAGE = 'no usage data';
const EMPTY_WIDTH_FALLBACKS = ['no usage', 'empty', '—'] as const;
/** Empty state: key row + message row; populated state needs a provider block below the key. */
const EMPTY_KEY_MIN_INNER_H = 2;
/** Short, wide panes should spend the width on columns instead of vertical stacking. */
const SPREAD_MAX_INNER_H = 5;
/** Keep spread columns readable before the gauges themselves start collapsing. */
const SPREAD_MIN_COLUMN_WIDTH = 14;
const SPREAD_COLUMN_GAP = 1;

export type UsageDisplayMode = 'full' | 'compact' | 'minimal' | 'tiny';

export type UsagePaddingTier = 'comfortable' | 'tight';

/** Horizontal padding budget per display tier — centralizes gutter, chrome, and label gaps. */
export interface UsageLayoutOptions {
  readonly tier: UsagePaddingTier;
  /** Horizontal chrome: side borders + horizontal padding. */
  readonly chromeWidth: number;
  /** Cursor-marker column budget on each gauge/key line. */
  readonly gutterWidth: number;
  /** Selected marker `▌` gets a trailing space before the window label. */
  readonly markerSpaced: boolean;
  readonly windowLabelGap: number;
  readonly barTrailGap: number;
  readonly panePaddingLeft: number;
  readonly panePaddingRight: number;
}

const PADDING_BY_TIER: Record<UsagePaddingTier, Omit<UsageLayoutOptions, 'tier'>> = {
  comfortable: {
    chromeWidth: 4,
    gutterWidth: 2,
    markerSpaced: true,
    windowLabelGap: 1,
    barTrailGap: BAR_TRAIL_GAP,
    panePaddingLeft: 1,
    panePaddingRight: 1,
  },
  tight: {
    chromeWidth: 2,
    gutterWidth: 1,
    markerSpaced: false,
    windowLabelGap: 0,
    barTrailGap: BAR_TRAIL_GAP,
    panePaddingLeft: 0,
    panePaddingRight: 0,
  },
};

export interface UsageSurfaceGauge {
  readonly label: string;
  readonly pct: number;
  readonly reset: string;
}

export interface UsageSurfaceGroup {
  readonly harness: string;
  readonly steering: string;
  /** Pre-formatted relative snapshot age for the harness header, e.g. `'2m ago'`. */
  readonly fetchedAt?: string;
  readonly gauges: readonly UsageSurfaceGauge[];
}

export interface UsageSurfaceProps {
  readonly width: number;
  readonly height: number;
  readonly focused: boolean;
  readonly theme: Theme;
  readonly groups: readonly UsageSurfaceGroup[];
  readonly cursor?: number;
  readonly status?: 'ready' | 'loading' | 'error';
  readonly error?: string | null;
}

interface GaugeGeometry {
  readonly windowLabel: string;
  readonly periodLabel: string;
  readonly resetLabel: string;
  readonly pctLabel: string;
  readonly barWidth: number;
  readonly filledCount: number;
  readonly isHigh: boolean;
}

interface GaugeLayoutOptions {
  readonly showLabel: boolean;
  readonly showWin: boolean;
  readonly showReset: boolean;
  readonly keyWindowLabel: 'window' | 'wind' | null;
  readonly keyLabelWidth: number;
}

export interface GroupGaugeLayout {
  readonly labelWidth: number;
  readonly barWidth: number;
}

export interface GaugeLayout extends GaugeLayoutOptions {
  readonly keyBarWidth: number;
  readonly groupLayouts: ReadonlyMap<string, GroupGaugeLayout>;
}

/** Keep empty chrome on one line — shorten before truncate so narrow panes stay intentional. */
export function formatEmptyMessage(text: string, budget: number): string {
  const cols = Math.max(0, budget);
  if (cols === 0) {
    return '';
  }
  if (text.length <= cols) {
    return text;
  }
  for (const fallback of EMPTY_WIDTH_FALLBACKS) {
    if (fallback.length <= cols) {
      return fallback;
    }
  }
  if (cols <= 1) {
    return '…';
  }
  return `${text.slice(0, cols - 1)}…`;
}

function emptyShowKeyLine(
  displayMode: UsageDisplayMode,
  gaugeLineLayout: GaugeLayout,
  innerH: number,
): boolean {
  return displayMode !== 'tiny' && gaugeLineLayout.showReset && innerH >= EMPTY_KEY_MIN_INNER_H;
}

function longestLabelInGroup(gauges: readonly UsageSurfaceGauge[]): number {
  return gauges.reduce((max, gauge) => Math.max(max, gauge.label.length), 0);
}

function keyLabelWidthForGroups(
  groups: readonly UsageSurfaceGroup[],
  keyWindowLabel: 'window' | 'wind' | null,
): number {
  const natural = groups.reduce(
    (max, group) => Math.max(max, longestLabelInGroup(group.gauges)),
    0,
  );
  const keyLen = keyWindowLabel?.length ?? 0;
  return Math.max(natural, keyLen);
}

function trailBlockWidth(
  showWin: boolean,
  showReset: boolean,
  padding: UsageLayoutOptions,
): number {
  if (!showWin && !showReset) {
    return 0;
  }
  if (showWin && showReset) {
    if (padding.tier === 'tight') {
      return padding.barTrailGap + WIN_WIDTH + TIGHT_WIN_RESET_GAP + RESET_WIDTH;
    }
    return padding.barTrailGap + WIN_WIDTH + RESET_TRAIL_SLOT;
  }
  if (showReset) {
    return padding.barTrailGap + RESET_WIDTH;
  }
  return padding.barTrailGap + WIN_WIDTH;
}

function winResetGap(padding: UsageLayoutOptions, resetLength: number): number {
  if (padding.tier === 'tight') {
    return TIGHT_WIN_RESET_GAP;
  }
  return RESET_TRAIL_SLOT - resetLength;
}

function gaugeBarWidth(
  innerWidth: number,
  labelWidth: number,
  showWin: boolean,
  showReset: boolean,
  padding: UsageLayoutOptions,
): number {
  let width = innerWidth - padding.gutterWidth - trailBlockWidth(showWin, showReset, padding);
  if (labelWidth > 0) {
    width -= labelWidth + padding.windowLabelGap;
  }
  return width;
}

function formatGaugeTrail(
  periodLabel: string,
  resetLabel: string,
  showWin: boolean,
  showReset: boolean,
  padding: UsageLayoutOptions,
): string {
  if (!showWin && !showReset) {
    return '';
  }
  if (showWin && showReset) {
    const win = periodLabel.slice(0, WIN_WIDTH).padEnd(WIN_WIDTH);
    const reset = resetLabel.slice(0, RESET_WIDTH);
    const gap = winResetGap(padding, reset.length);
    return `${' '.repeat(padding.barTrailGap)}${win}${' '.repeat(gap)}${reset}`;
  }
  if (showReset) {
    return `${' '.repeat(padding.barTrailGap)}${resetLabel.slice(0, RESET_WIDTH).padStart(RESET_WIDTH)}`;
  }
  return `${' '.repeat(padding.barTrailGap)}${periodLabel.slice(0, WIN_WIDTH).padEnd(WIN_WIDTH)}`;
}

function formatKeyTrail(showWin: boolean, showReset: boolean, padding: UsageLayoutOptions): string {
  if (!showWin && !showReset) {
    return '';
  }
  if (showWin && showReset) {
    const gap = winResetGap(padding, 'reset'.length);
    return `${' '.repeat(padding.barTrailGap)}${'win'.padEnd(WIN_WIDTH)}${' '.repeat(gap)}reset`;
  }
  if (showReset) {
    return `${' '.repeat(padding.barTrailGap)}${'reset'.padStart(RESET_WIDTH)}`;
  }
  return `${' '.repeat(padding.barTrailGap)}${'win'.padEnd(WIN_WIDTH)}`;
}

/** Pick horizontal padding tier from allocation width (same cutoff for every display mode). */
export function layoutOptionsFor(width: number): UsageLayoutOptions {
  const comfortableInner = Math.max(1, width - PADDING_BY_TIER.comfortable.chromeWidth);
  const tier: UsagePaddingTier =
    comfortableInner > TIGHT_PADDING_MAX_COMFORTABLE_INNER_W ? 'comfortable' : 'tight';
  return { tier, ...PADDING_BY_TIER[tier] };
}

function contentWidth(width: number, padding: UsageLayoutOptions): number {
  return Math.max(1, width - padding.chromeWidth);
}

function contentHeight(height: number): number {
  return Math.max(0, height - CHROME_HEIGHT);
}

/** Deterministic 2D layout router — richest display at the largest allocation. */
export function layout(width: number, height: number): UsageDisplayMode {
  const modeInnerW = Math.max(1, width - PADDING_BY_TIER.comfortable.chromeWidth);
  const innerH = contentHeight(height);
  if (innerH < 2 || modeInnerW < 7) {
    return 'tiny';
  }
  if (innerH < 4 || modeInnerW < 16) {
    return 'minimal';
  }
  if (innerH < 6 || modeInnerW < 22) {
    return 'compact';
  }
  return 'full';
}

function gaugeGutterPrefix(selected: boolean, padding: UsageLayoutOptions): string {
  const marker = selected ? '▌' : ' ';
  return padding.markerSpaced ? `${marker} ` : marker;
}

function gaugeLayoutFor(
  innerWidth: number,
  groups: readonly UsageSurfaceGroup[],
  padding: UsageLayoutOptions,
): GaugeLayout {
  const attempts: readonly {
    readonly keyWindowLabel: 'window' | 'wind' | null;
    readonly showWin: boolean;
    readonly showReset: boolean;
  }[] = [
    { keyWindowLabel: 'window', showWin: true, showReset: true },
    { keyWindowLabel: 'window', showWin: false, showReset: true },
    { keyWindowLabel: 'wind', showWin: true, showReset: true },
    { keyWindowLabel: 'wind', showWin: false, showReset: true },
    { keyWindowLabel: null, showWin: true, showReset: true },
    { keyWindowLabel: null, showWin: false, showReset: true },
    { keyWindowLabel: 'window', showWin: false, showReset: false },
    { keyWindowLabel: 'wind', showWin: false, showReset: false },
    { keyWindowLabel: null, showWin: false, showReset: false },
  ];

  for (const attempt of attempts) {
    if (padding.tier === 'tight' && attempt.keyWindowLabel !== null) {
      continue;
    }
    const keyLabelWidth = attempt.keyWindowLabel
      ? keyLabelWidthForGroups(groups, attempt.keyWindowLabel)
      : 0;
    const labelWidthForLayout = attempt.keyWindowLabel ? keyLabelWidth : 0;
    const keyBarWidth = gaugeBarWidth(
      innerWidth,
      labelWidthForLayout,
      attempt.showWin,
      attempt.showReset,
      padding,
    );
    if (keyBarWidth < MIN_GAUGE_BAR_WIDTH) {
      continue;
    }

    const groupLayouts = new Map<string, GroupGaugeLayout>();
    let allGroupsFit = true;
    for (const group of groups) {
      const labelWidth = attempt.keyWindowLabel ? longestLabelInGroup(group.gauges) : 0;
      const barWidth = gaugeBarWidth(
        innerWidth,
        labelWidth,
        attempt.showWin,
        attempt.showReset,
        padding,
      );
      if (barWidth < MIN_GAUGE_BAR_WIDTH) {
        allGroupsFit = false;
        break;
      }
      groupLayouts.set(group.harness, { labelWidth, barWidth });
    }
    if (!allGroupsFit) {
      continue;
    }

    return {
      showLabel: labelWidthForLayout > 0,
      showWin: attempt.showWin,
      showReset: attempt.showReset,
      keyWindowLabel: attempt.keyWindowLabel,
      keyLabelWidth: labelWidthForLayout,
      keyBarWidth,
      groupLayouts,
    };
  }

  const fallbackBar = Math.max(1, innerWidth - padding.gutterWidth);
  const groupLayouts = new Map<string, GroupGaugeLayout>();
  for (const group of groups) {
    groupLayouts.set(group.harness, { labelWidth: 0, barWidth: fallbackBar });
  }
  return {
    showLabel: false,
    showWin: false,
    showReset: false,
    keyWindowLabel: null,
    keyLabelWidth: 0,
    keyBarWidth: fallbackBar,
    groupLayouts,
  };
}

function toGaugeGeometry(gauge: UsageSurfaceGauge): GaugeGeometry {
  const pct = Math.min(Math.max(gauge.pct, 0), 100);
  return {
    windowLabel: gauge.label,
    periodLabel: /^\d+[hdm]$/.test(gauge.label) ? gauge.label : '',
    resetLabel: gauge.reset,
    pctLabel: `${pct}%`,
    barWidth: USAGE_BAR_WIDTH,
    filledCount: Math.round((pct / 100) * USAGE_BAR_WIDTH),
    isHigh: pct >= 80,
  };
}

function renderBar(g: GaugeGeometry, theme: Theme, width: number): React.JSX.Element {
  const filledColor = g.isHigh ? theme.gaugeHigh : theme.gaugeNormal;
  const filledCount = Math.min(width, Math.round((g.filledCount * width) / g.barWidth));
  const label = g.pctLabel.length <= width ? g.pctLabel : g.pctLabel.slice(0, width);
  if (filledCount >= Math.max(MIN_EMBED_FILL, label.length)) {
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

function steeringTag(steering: string): string {
  if (steering === 'pause') return ' [paused]';
  if (steering === 'prefer') return ' [preferred]';
  return '';
}

function headerFetchedAtBudget(
  innerWidth: number,
  harness: string,
  steering: string,
  fetchedAt: string,
): boolean {
  const prefix = 2;
  const needed = prefix + harness.length + steeringTag(steering).length + 1 + fetchedAt.length;
  return innerWidth >= needed;
}

function shouldShowFetchedAt(
  displayMode: UsageDisplayMode,
  innerWidth: number,
  harness: string,
  steering: string,
  fetchedAt: string | undefined,
): boolean {
  if (fetchedAt === undefined || fetchedAt === '') {
    return false;
  }
  if (displayMode !== 'full') {
    return false;
  }
  return headerFetchedAtBudget(innerWidth, harness, steering, fetchedAt);
}

function HeaderLine({
  harness,
  steering,
  compact,
  theme,
  fetchedAt,
  showFetchedAt,
}: {
  readonly harness: string;
  readonly steering: string;
  readonly compact: boolean;
  readonly theme: Theme;
  readonly fetchedAt?: string;
  readonly showFetchedAt: boolean;
}): React.JSX.Element {
  const tag = steeringTag(steering);
  const fetchedSuffix =
    showFetchedAt && fetchedAt !== undefined && fetchedAt !== '' ? (
      <Text dimColor>{`  ${fetchedAt}`}</Text>
    ) : null;
  if (compact) {
    return (
      <Box flexShrink={0} width="100%">
        <Text dimColor wrap="truncate">
          {`· ${harness}`}
          {tag ? <Text color={theme.accent}>{tag}</Text> : null}
          {fetchedSuffix}
        </Text>
      </Box>
    );
  }
  return (
    <Box flexShrink={0} width="100%" backgroundColor={theme.panelHeaderBg}>
      <Text bold wrap="truncate">
        {` ${harness}`}
        {tag ? <Text color={theme.accent}>{tag}</Text> : null}
        {fetchedSuffix}
      </Text>
    </Box>
  );
}

function GaugeLine({
  gauge,
  selected,
  layout: gaugeLineLayout,
  groupLayout,
  padding,
  theme,
}: {
  readonly gauge: GaugeGeometry;
  readonly selected: boolean;
  readonly layout: GaugeLayout;
  readonly groupLayout: GroupGaugeLayout;
  readonly padding: UsageLayoutOptions;
  readonly theme: Theme;
}): React.JSX.Element {
  const gutter = gaugeGutterPrefix(selected, padding);
  const trail = formatGaugeTrail(
    gauge.periodLabel,
    gauge.resetLabel,
    gaugeLineLayout.showWin,
    gaugeLineLayout.showReset,
    padding,
  );
  return (
    <Box flexShrink={0} width="100%" backgroundColor={selected ? theme.panelSelectedBg : undefined}>
      <Text wrap="truncate">
        {gutter}
        {gaugeLineLayout.showLabel ? (
          <>
            <Text dimColor>
              {gauge.windowLabel.slice(0, groupLayout.labelWidth).padEnd(groupLayout.labelWidth)}
            </Text>
            {padding.windowLabelGap > 0 ? ' ' : null}
          </>
        ) : null}
        {renderBar(gauge, theme, groupLayout.barWidth)}
        {trail ? <Text dimColor>{trail}</Text> : null}
      </Text>
    </Box>
  );
}

function EmptyUsageState({
  innerW,
  innerH,
  displayMode,
  gaugeLineLayout,
  padding,
}: {
  readonly innerW: number;
  readonly innerH: number;
  readonly displayMode: UsageDisplayMode;
  readonly gaugeLineLayout: GaugeLayout;
  readonly padding: UsageLayoutOptions;
}): React.JSX.Element {
  const message = formatEmptyMessage(EMPTY_MESSAGE, innerW);
  const showKey = emptyShowKeyLine(displayMode, gaugeLineLayout, innerH);

  if (innerH < 1 || message.length === 0) {
    return <Text dimColor> </Text>;
  }
  if (showKey) {
    return (
      <Box flexDirection="column" flexShrink={0} height={innerH} overflow="hidden">
        <UsageKeyLine layout={gaugeLineLayout} padding={padding} />
        <Text dimColor wrap="truncate">
          {message}
        </Text>
      </Box>
    );
  }
  return (
    <Text dimColor wrap="truncate">
      {message}
    </Text>
  );
}

function UsageKeyLine({
  layout: gaugeLineLayout,
  padding,
}: {
  readonly layout: GaugeLayout;
  readonly padding: UsageLayoutOptions;
}): React.JSX.Element | null {
  if (!gaugeLineLayout.showReset) {
    return null;
  }
  const trail = formatKeyTrail(gaugeLineLayout.showWin, gaugeLineLayout.showReset, padding);
  return (
    <Box flexShrink={0} width="100%">
      <Text dimColor wrap="truncate">
        {' '.repeat(padding.gutterWidth)}
        {gaugeLineLayout.keyWindowLabel
          ? `${gaugeLineLayout.keyWindowLabel.padEnd(gaugeLineLayout.keyLabelWidth)}${padding.windowLabelGap > 0 ? ' ' : ''}`
          : ''}
        {'usage'.padEnd(gaugeLineLayout.keyBarWidth)}
        {trail}
      </Text>
    </Box>
  );
}

type FlatGauge = {
  readonly groupIndex: number;
  readonly gaugeIndex: number;
  readonly geometry: GaugeGeometry;
};

function flattenGauges(groups: readonly UsageSurfaceGroup[]): readonly FlatGauge[] {
  const flat: FlatGauge[] = [];
  for (let groupIndex = 0; groupIndex < groups.length; groupIndex += 1) {
    const group = groups[groupIndex];
    if (group === undefined) continue;
    for (let gaugeIndex = 0; gaugeIndex < group.gauges.length; gaugeIndex += 1) {
      const gauge = group.gauges[gaugeIndex];
      if (gauge === undefined) continue;
      flat.push({ groupIndex, gaugeIndex, geometry: toGaugeGeometry(gauge) });
    }
  }
  return flat;
}

function countGauges(groups: readonly UsageSurfaceGroup[]): number {
  return groups.reduce((n, g) => n + g.gauges.length, 0);
}

function visibleGroupWindow(
  groups: readonly UsageSurfaceGroup[],
  mode: UsageDisplayMode,
  innerH: number,
  showKeyLine: boolean,
): readonly UsageSurfaceGroup[] {
  if (mode === 'tiny') {
    return groups.slice(0, 1);
  }
  const keyLines = showKeyLine ? 1 : 0;
  let budget = innerH - keyLines;
  const visible: UsageSurfaceGroup[] = [];
  for (const group of groups) {
    const blockLines = 1 + group.gauges.length;
    if (budget < 1) break;
    if (blockLines > budget && visible.length > 0) break;
    visible.push(
      blockLines > budget
        ? { ...group, gauges: group.gauges.slice(0, Math.max(1, budget - 1)) }
        : group,
    );
    budget -= blockLines;
  }
  return visible;
}

function shouldSpreadGroups(innerW: number, innerH: number, groupCount: number): boolean {
  if (groupCount <= 1) {
    return false;
  }
  if (innerH > SPREAD_MAX_INNER_H) {
    return false;
  }
  const requiredWidth =
    groupCount * SPREAD_MIN_COLUMN_WIDTH + Math.max(0, groupCount - 1) * SPREAD_COLUMN_GAP;
  return innerW >= requiredWidth;
}

function shouldSpreadSingleGroup(innerW: number, innerH: number, gaugeCount: number): boolean {
  return gaugeCount > 1 && shouldSpreadGroups(innerW, innerH, gaugeCount);
}

function spreadColumnsForSingleGroup(group: UsageSurfaceGroup): readonly UsageSurfaceGroup[] {
  if (group.gauges.length <= 1) {
    return [group];
  }
  return group.gauges.map((gauge) => ({
    harness: group.harness,
    steering: group.steering,
    gauges: [gauge],
  }));
}

function splitWidths(totalWidth: number, count: number): number[] {
  if (count <= 0) {
    return [];
  }
  const usable = Math.max(count, totalWidth - Math.max(0, count - 1) * SPREAD_COLUMN_GAP);
  const base = Math.floor(usable / count);
  let remainder = usable % count;
  return Array.from({ length: count }, () => {
    const width = base + (remainder > 0 ? 1 : 0);
    remainder -= 1;
    return Math.max(1, width);
  });
}

function SpreadGroupColumn({
  group,
  width,
  cursor,
  focused,
  padding,
  innerH,
  gaugeIndexStart,
  theme,
}: {
  readonly group: UsageSurfaceGroup;
  readonly width: number;
  readonly cursor: number;
  readonly focused: boolean;
  readonly padding: UsageLayoutOptions;
  readonly innerH: number;
  readonly gaugeIndexStart: number;
  readonly theme: Theme;
}): React.JSX.Element {
  const gaugeLineLayout = useMemo(
    () => gaugeLayoutFor(width, [group], padding),
    [group, padding, width],
  );
  const headerCompact = width < 18 || !gaugeLineLayout.showReset;
  const maxGauges = Math.max(0, innerH - 1);
  const gauges = group.gauges.slice(0, maxGauges);
  let gaugeIndex = gaugeIndexStart;

  return (
    <Box flexDirection="column" flexShrink={0} width={width}>
      <HeaderLine
        harness={group.harness}
        steering={group.steering}
        compact={headerCompact}
        theme={theme}
        fetchedAt={group.fetchedAt}
        showFetchedAt={false}
      />
      {gauges.map((gauge) => {
        gaugeIndex += 1;
        return (
          <GaugeLine
            key={`${group.harness}-${gauge.label}`}
            gauge={toGaugeGeometry(gauge)}
            selected={focused && gaugeIndex === cursor}
            layout={gaugeLineLayout}
            groupLayout={
              gaugeLineLayout.groupLayouts.get(group.harness) ?? {
                labelWidth: 0,
                barWidth: gaugeLineLayout.keyBarWidth,
              }
            }
            padding={padding}
            theme={theme}
          />
        );
      })}
    </Box>
  );
}

function renderSpreadGroups({
  groups,
  cursor,
  focused,
  innerW,
  innerH,
  padding,
  theme,
}: {
  readonly groups: readonly UsageSurfaceGroup[];
  readonly cursor: number;
  readonly focused: boolean;
  readonly innerW: number;
  readonly innerH: number;
  readonly padding: UsageLayoutOptions;
  readonly theme: Theme;
}): React.ReactNode {
  const widths = splitWidths(innerW, groups.length);
  let gaugeIndexStart = -1;
  return (
    <Box flexDirection="row" flexShrink={0} width="100%" overflow="hidden">
      {groups.map((group, index) => {
        const width = widths[index] ?? 1;
        const node = (
          <SpreadGroupColumn
            key={group.harness}
            group={group}
            width={width}
            cursor={cursor}
            focused={focused}
            padding={padding}
            innerH={innerH}
            gaugeIndexStart={gaugeIndexStart}
            theme={theme}
          />
        );
        gaugeIndexStart += group.gauges.length;
        return (
          <Fragment key={group.harness}>
            {node}
            {index < groups.length - 1 ? <Box width={SPREAD_COLUMN_GAP} flexShrink={0} /> : null}
          </Fragment>
        );
      })}
    </Box>
  );
}

function UsageBody({
  groups,
  cursor,
  focused,
  width,
  height,
  displayMode,
  status,
  error,
  theme,
}: {
  readonly groups: readonly UsageSurfaceGroup[];
  readonly cursor: number;
  readonly focused: boolean;
  readonly width: number;
  readonly height: number;
  readonly displayMode: UsageDisplayMode;
  readonly status: 'ready' | 'loading' | 'error';
  readonly error: string | null;
  readonly theme: Theme;
}): React.JSX.Element {
  const padding = useMemo(() => layoutOptionsFor(width), [width]);
  const innerW = contentWidth(width, padding);
  const innerH = contentHeight(height);
  const gaugeLineLayout = useMemo(
    () => gaugeLayoutFor(innerW, groups, padding),
    [innerW, groups, padding],
  );
  const compactHeader = displayMode !== 'full' || !gaugeLineLayout.showReset;
  const showKeyLine =
    displayMode === 'full' && gaugeLineLayout.showReset && innerH >= FULL_KEY_MIN_INNER_H;
  const flat = useMemo(() => flattenGauges(groups), [groups]);
  const spreadSingleGroup =
    groups.length === 1 && groups[0] !== undefined
      ? shouldSpreadSingleGroup(innerW, innerH, groups[0].gauges.length)
      : false;
  const spreadGroups = shouldSpreadGroups(innerW, innerH, groups.length);

  if (status === 'error') {
    return <Text color={theme.error}>{`error: ${error ?? 'unknown'} (r to retry)`}</Text>;
  }
  if (status === 'loading' && groups.length === 0) {
    return <Text dimColor>loading…</Text>;
  }
  if (groups.length === 0) {
    return (
      <EmptyUsageState
        innerW={innerW}
        innerH={innerH}
        displayMode={displayMode}
        gaugeLineLayout={gaugeLineLayout}
        padding={padding}
      />
    );
  }

  if (innerH < 1) {
    return <Text dimColor> </Text>;
  }

  if (displayMode === 'tiny') {
    const gauge = flat[Math.min(cursor, Math.max(flat.length - 1, 0))];
    if (gauge === undefined) {
      return (
        <EmptyUsageState
          innerW={innerW}
          innerH={innerH}
          displayMode={displayMode}
          gaugeLineLayout={gaugeLineLayout}
          padding={padding}
        />
      );
    }
    const group = groups[gauge.groupIndex];
    const groupLayout = gaugeLineLayout.groupLayouts.get(group?.harness ?? '') ?? {
      labelWidth: 0,
      barWidth: gaugeLineLayout.keyBarWidth,
    };
    if (innerH < 2) {
      return (
        <GaugeLine
          gauge={gauge.geometry}
          selected={focused}
          layout={gaugeLineLayout}
          groupLayout={groupLayout}
          padding={padding}
          theme={theme}
        />
      );
    }
    return (
      <Box flexDirection="column" flexShrink={0}>
        <HeaderLine
          harness={group?.harness ?? '?'}
          steering={group?.steering ?? 'auto'}
          compact
          theme={theme}
          fetchedAt={group?.fetchedAt}
          showFetchedAt={shouldShowFetchedAt(
            displayMode,
            innerW,
            group?.harness ?? '?',
            group?.steering ?? 'auto',
            group?.fetchedAt,
          )}
        />
        <GaugeLine
          gauge={gauge.geometry}
          selected={focused}
          layout={gaugeLineLayout}
          groupLayout={groupLayout}
          padding={padding}
          theme={theme}
        />
      </Box>
    );
  }

  if (spreadGroups || spreadSingleGroup) {
    const spreadSourceGroups = spreadSingleGroup
      ? spreadColumnsForSingleGroup(groups[0] as UsageSurfaceGroup)
      : groups;
    return (
      <Box flexDirection="column" flexShrink={0} height={innerH} overflow="hidden">
        {renderSpreadGroups({
          groups: spreadSourceGroups,
          cursor,
          focused,
          innerW,
          innerH,
          padding,
          theme,
        })}
      </Box>
    );
  }

  const visibleGroups = visibleGroupWindow(groups, displayMode, innerH, showKeyLine);
  let gaugeIndex = -1;

  return (
    <Box flexDirection="column" flexShrink={0} height={innerH} overflow="hidden">
      {showKeyLine ? <UsageKeyLine layout={gaugeLineLayout} padding={padding} /> : null}
      {visibleGroups.map((group) => (
        <Box key={group.harness} flexDirection="column" flexShrink={0}>
          <HeaderLine
            harness={group.harness}
            steering={group.steering}
            compact={compactHeader}
            theme={theme}
            fetchedAt={group.fetchedAt}
            showFetchedAt={shouldShowFetchedAt(
              displayMode,
              innerW,
              group.harness,
              group.steering,
              group.fetchedAt,
            )}
          />
          {group.gauges.map((gauge) => {
            gaugeIndex += 1;
            const groupLayout = gaugeLineLayout.groupLayouts.get(group.harness) ?? {
              labelWidth: 0,
              barWidth: gaugeLineLayout.keyBarWidth,
            };
            return (
              <GaugeLine
                key={`${group.harness}-${gauge.label}`}
                gauge={toGaugeGeometry(gauge)}
                selected={focused && gaugeIndex === cursor}
                layout={gaugeLineLayout}
                groupLayout={groupLayout}
                padding={padding}
                theme={theme}
              />
            );
          })}
        </Box>
      ))}
    </Box>
  );
}

export const UsageSurface = memo(function UsageSurface({
  width,
  height,
  focused,
  theme,
  groups,
  cursor: cursorProp = 0,
  status = 'ready',
  error = null,
}: UsageSurfaceProps): React.JSX.Element {
  const displayMode = layout(width, height);
  const gaugeCount = countGauges(groups);
  const cursor = Math.min(cursorProp, Math.max(gaugeCount - 1, 0));
  const padding = layoutOptionsFor(width);

  return (
    <Box width={width} height={height} flexDirection="column" overflow="hidden">
      <Pane
        title={PANEL_TITLE}
        focused={focused}
        flexGrow={1}
        paddingLeft={padding.panePaddingLeft}
        paddingRight={padding.panePaddingRight}
      >
        <UsageBody
          groups={groups}
          cursor={cursor}
          focused={focused}
          width={width}
          height={height}
          displayMode={displayMode}
          status={status}
          error={error}
          theme={theme}
        />
      </Pane>
    </Box>
  );
});
