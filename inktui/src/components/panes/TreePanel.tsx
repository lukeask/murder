/**
 * TreePanel — Git Tree swimlane DAG (fixture-friendly pane contract).
 *
 * Accepts explicit allocated `width`/`height` (full pane size incl. border/title/padding) and
 * degrades via a local `layout(width, height)` router. Phase 0 renders from pre-built fixture rows;
 * live store wiring lands in a later phase.
 */

import { Box, Text } from 'ink';
import { memo } from 'react';
import { useTheme } from '../../theme/themeStore.js';
import { Pane, paneContentWidthForWidth, paneHorizontalPaddingForWidth } from '../Pane.js';

const PANEL_TITLE = 'Git Tree';

/** Vertical chrome: inline title row + bottom border (matches fixture `innerHeight`). */
const CHROME_HEIGHT = 2;

/** Gap between railway grid and branch tag column. */
const TAG_GAP = 1;

const ARROW_GLYPHS = '▶';
const TRIANGLE_GLYPHS = '○◆┳╰';
const MIN_RAIL_WIDTH = 3;
const MIN_TAG_WIDTH = 3;
const MIN_NAME_INTERIOR = 6;
/** Below this inner width, stack hash / branch / age vertically instead of `a · b · c`. */
const INFO_STACK_INNER_W = 30;
/** Below this inner width, drop interior spaces in `▐name▌` branch tags. */
const TAG_TIGHT_INNER_W = 36;

export interface TreePanelLane {
  readonly branch: string;
  readonly rail: string;
  readonly color: string;
  readonly selected?: boolean;
}

export interface TreePanelData {
  readonly ruler: string;
  readonly lanes: readonly TreePanelLane[];
  readonly info: readonly string[];
  readonly pending?: boolean;
  readonly status?: 'idle' | 'loading' | 'ready' | 'error';
  readonly error?: string | null;
}

export type TransitDisplayMode = 'full' | 'compact' | 'lanesOnly' | 'narrow' | 'minimal' | 'micro';
export type TransitTagPadding = 'padded' | 'tight';
export type TransitInfoLayout = 'inline' | 'stacked';

export interface TransitLayoutPlan {
  readonly mode: TransitDisplayMode;
  readonly infoLayout: TransitInfoLayout;
  readonly tagPadding: TransitTagPadding;
}

export interface TransitBodyPlan extends TransitLayoutPlan {
  readonly showRuler: boolean;
  readonly lanes: readonly TreePanelLane[];
  readonly infoLines: readonly string[];
  readonly showInfoGap: boolean;
  readonly infoRenderLayout: TransitInfoLayout;
}

export interface TreePanelProps {
  readonly width: number;
  readonly height: number;
  readonly focused: boolean;
  readonly data: TreePanelData;
}

interface LaneRowGeometry {
  readonly tagColWidth: number;
  readonly railwayWidth: number;
  readonly tag: string;
  readonly rail: string;
}

function contentInnerWidth(width: number): number {
  return paneContentWidthForWidth(width);
}

function contentInnerHeight(height: number): number {
  return Math.max(1, height - CHROME_HEIGHT);
}

function hasRailGlyphs(text: string): boolean {
  let hasArrow = false;
  let hasTriangle = false;
  for (const ch of text) {
    if (ARROW_GLYPHS.includes(ch)) {
      hasArrow = true;
    }
    if (TRIANGLE_GLYPHS.includes(ch)) {
      hasTriangle = true;
    }
    if (hasArrow && hasTriangle) {
      return true;
    }
  }
  return false;
}

/** Clip a pre-built rail from the left so the newest commits stay on the right. */
export function fitRail(rail: string, maxWidth: number): string {
  if (maxWidth <= 0) {
    return '';
  }
  if (rail.length <= maxWidth) {
    return rail;
  }

  let arrowIdx = -1;
  let triIdx = -1;
  for (let i = rail.length - 1; i >= 0; i -= 1) {
    const ch = rail[i] ?? '';
    if (arrowIdx < 0 && ARROW_GLYPHS.includes(ch)) {
      arrowIdx = i;
    }
    if (triIdx < 0 && TRIANGLE_GLYPHS.includes(ch)) {
      triIdx = i;
    }
    if (arrowIdx >= 0 && triIdx >= 0) {
      break;
    }
  }

  if (arrowIdx < 0 && triIdx < 0) {
    return rail.slice(-maxWidth);
  }

  const glyphStart = Math.min(
    arrowIdx < 0 ? rail.length : arrowIdx,
    triIdx < 0 ? rail.length : triIdx,
  );
  const windowStart = Math.max(0, rail.length - maxWidth);
  const start = Math.min(glyphStart, windowStart);
  let result = rail.slice(start, start + maxWidth);

  if (!hasRailGlyphs(result)) {
    const tri = triIdx >= 0 ? (rail[triIdx] ?? '○') : '○';
    const arrow = arrowIdx >= 0 ? (rail[arrowIdx] ?? '▶') : '▶';
    if (maxWidth >= 3) {
      result = `${tri}━${arrow}`.padStart(maxWidth, ' ');
    } else if (maxWidth >= 2) {
      result = `${tri}${arrow}`;
    } else {
      result = arrow;
    }
  }

  return result.length <= maxWidth ? result : result.slice(0, maxWidth);
}

/** Interior branch label — ≥6 leading chars when the name is longer than 6. */
export function clipBranchInterior(branch: string, maxInterior: number): string {
  if (maxInterior <= 0) {
    return '';
  }
  if (branch.length <= maxInterior) {
    return branch;
  }
  if (maxInterior >= 7) {
    return `${branch.slice(0, maxInterior - 1)}…`;
  }
  if (maxInterior >= MIN_NAME_INTERIOR) {
    return branch.slice(0, MIN_NAME_INTERIOR);
  }
  return branch.slice(0, maxInterior);
}

/** `▐ name ▌` (padded) or `▐name▌` (tight); bars preserved; interior ellipsis only. */
export function formatBranchTag(
  branch: string,
  tagColWidth: number,
  padding: TransitTagPadding = 'padded',
): string {
  if (tagColWidth <= 0) {
    return '';
  }
  if (padding === 'tight') {
    if (tagColWidth <= 1) {
      return '▐'.slice(0, tagColWidth);
    }
    if (tagColWidth === 2) {
      return '▐▌';
    }
    const maxInterior = Math.max(0, tagColWidth - 2);
    const interior = clipBranchInterior(branch, maxInterior);
    const tag = `▐${interior}▌`;
    return tag.length <= tagColWidth ? tag : tag.slice(0, tagColWidth);
  }
  if (tagColWidth <= 3) {
    return '▐…▌'.slice(0, tagColWidth);
  }
  const maxInterior = Math.max(0, tagColWidth - 4);
  const interior = clipBranchInterior(branch, maxInterior);
  const tag = `▐ ${interior} ▌`;
  if (tag.length <= tagColWidth) {
    return tag;
  }
  return tag.slice(0, tagColWidth);
}

function desiredTagWidth(branch: string, padding: TransitTagPadding): number {
  if (padding === 'tight') {
    return Math.max(MIN_TAG_WIDTH, `▐${branch}▌`.length);
  }
  return Math.max(MIN_TAG_WIDTH, `▐ ${branch} ▌`.length);
}

/** Split inner width between the shared tag column and the railway (newest commits on the right). */
export function allocateLaneColumns(
  innerW: number,
  lanes: readonly TreePanelLane[],
  tagPadding: TransitTagPadding = 'padded',
): { tagColWidth: number; railwayWidth: number } {
  const barOverhead = tagPadding === 'tight' ? 2 : 4;
  const maxUsefulTag = lanes.reduce(
    (max, lane) => Math.max(max, desiredTagWidth(lane.branch, tagPadding)),
    MIN_TAG_WIDTH,
  );
  const minTagForNames = lanes.reduce((max, lane) => {
    const interior =
      lane.branch.length <= MIN_NAME_INTERIOR ? lane.branch.length : MIN_NAME_INTERIOR;
    return Math.max(max, barOverhead + interior);
  }, MIN_TAG_WIDTH);
  const railPreferred = Math.max(
    MIN_RAIL_WIDTH,
    Math.min(innerW - MIN_TAG_WIDTH - TAG_GAP, Math.floor(innerW * 0.55)),
  );
  let railwayWidth = railPreferred;
  let tagColWidth = innerW - TAG_GAP - railwayWidth;
  if (tagColWidth > maxUsefulTag) {
    tagColWidth = maxUsefulTag;
    railwayWidth = innerW - TAG_GAP - tagColWidth;
  }
  if (innerW - TAG_GAP - MIN_RAIL_WIDTH >= minTagForNames) {
    tagColWidth = Math.max(tagColWidth, minTagForNames);
    railwayWidth = innerW - TAG_GAP - tagColWidth;
  }
  while (railwayWidth < MIN_RAIL_WIDTH && tagColWidth > MIN_TAG_WIDTH) {
    tagColWidth -= 1;
    railwayWidth = innerW - TAG_GAP - tagColWidth;
  }
  return {
    tagColWidth: Math.max(MIN_TAG_WIDTH, tagColWidth),
    railwayWidth: Math.max(MIN_RAIL_WIDTH, railwayWidth),
  };
}

export function laneRowGeometry(
  lane: TreePanelLane,
  innerW: number,
  lanes: readonly TreePanelLane[],
  tagPadding: TransitTagPadding = 'padded',
): LaneRowGeometry {
  const { tagColWidth, railwayWidth } = allocateLaneColumns(innerW, lanes, tagPadding);
  return {
    tagColWidth,
    railwayWidth,
    tag: formatBranchTag(lane.branch, tagColWidth, tagPadding),
    rail: fitRail(lane.rail, railwayWidth),
  };
}

/** Parse `hash · branch · age` metadata from the first info line. */
export function parseInfoMetaLine(
  line: string,
): { readonly hash: string; readonly branch: string; readonly age: string } | null {
  const parts = line.split(' · ');
  if (parts.length !== 3) {
    return null;
  }
  const hash = parts[0]?.trim() ?? '';
  const branch = parts[1]?.trim() ?? '';
  const age = parts[2]?.trim() ?? '';
  if (hash.length === 0 || branch.length === 0 || age.length === 0) {
    return null;
  }
  return { hash, branch, age };
}

function tagPaddingForWidth(innerW: number): TransitTagPadding {
  return innerW < TAG_TIGHT_INNER_W ? 'tight' : 'padded';
}

function infoLayoutForWidth(innerW: number): TransitInfoLayout {
  return innerW < INFO_STACK_INNER_W ? 'stacked' : 'inline';
}

/** Prefer stacked at narrow widths, but fall back to inline when row budget is too tight. */
function infoLayoutForBudget(innerW: number, rowBudget: number): TransitInfoLayout {
  if (innerW >= INFO_STACK_INNER_W) {
    return 'inline';
  }
  return rowBudget >= 3 ? 'stacked' : 'inline';
}

/** Visual row count for a sliced info block (stacked meta expands; inline may wrap). */
export function countInfoRenderLines(
  lines: readonly string[],
  infoLayout: TransitInfoLayout,
  pending: boolean,
  innerW?: number,
): number {
  if (lines.length === 0) {
    return 0;
  }
  const wrapW = innerW ?? Number.POSITIVE_INFINITY;
  const meta = !pending ? parseInfoMetaLine(lines[0] ?? '') : null;
  if (meta !== null && infoLayout === 'stacked') {
    let rows = 3;
    for (const line of lines.slice(1)) {
      rows += Math.max(1, Math.ceil(line.length / Math.max(1, wrapW)));
    }
    return rows;
  }
  if (meta !== null && infoLayout === 'inline') {
    const inline = `${meta.hash} · ${meta.branch} · ${meta.age}`;
    let rows = Math.max(1, Math.ceil(inline.length / Math.max(1, wrapW)));
    for (const line of lines.slice(1)) {
      rows += Math.max(1, Math.ceil(line.length / Math.max(1, wrapW)));
    }
    return rows;
  }
  // g-jump overlay lines truncate (see InfoSection); do not inflate row budget via wrap.
  if (pending) {
    return lines.length;
  }
  let rows = 0;
  for (const line of lines) {
    rows += Math.max(1, Math.ceil(line.length / Math.max(1, wrapW)));
  }
  return rows;
}

function windowLanes(lanes: readonly TreePanelLane[], maxLanes: number): readonly TreePanelLane[] {
  if (maxLanes <= 0 || lanes.length === 0) {
    return [];
  }
  if (lanes.length <= maxLanes) {
    return lanes;
  }
  const selectedIndex = Math.max(
    0,
    lanes.findIndex((lane) => lane.selected),
  );
  const start = Math.min(
    Math.max(0, selectedIndex - Math.floor(maxLanes / 2)),
    lanes.length - maxLanes,
  );
  return lanes.slice(start, start + maxLanes);
}

/** Slice info source lines so rendered rows fit `rowBudget`. */
function fitInfoSourceLines(
  info: readonly string[],
  infoLayout: TransitInfoLayout,
  pending: boolean,
  rowBudget: number,
  innerW: number,
): readonly string[] {
  if (rowBudget <= 0 || info.length === 0) {
    return [];
  }
  let best: readonly string[] = [];
  for (let count = 1; count <= info.length; count += 1) {
    const slice = info.slice(0, count);
    if (countInfoRenderLines(slice, infoLayout, pending, innerW) <= rowBudget) {
      best = slice;
    } else {
      break;
    }
  }
  return best;
}

interface BodyCandidate {
  readonly mode: TransitDisplayMode;
  readonly showRuler: boolean;
  readonly lanes: readonly TreePanelLane[];
  readonly infoLines: readonly string[];
  readonly showInfoGap: boolean;
  readonly infoRenderLayout: TransitInfoLayout;
  readonly score: number;
}

function scoreBodyCandidate(
  candidate: Omit<BodyCandidate, 'score'>,
  laneTotal: number,
  infoTotal: number,
  pending: boolean,
  innerW: number,
): number {
  const laneScore = candidate.lanes.length * (pending ? 75 : 100);
  const infoScore = countInfoRenderLines(
    candidate.infoLines,
    candidate.infoRenderLayout,
    pending,
    innerW,
  );
  const rulerBonus = candidate.showRuler ? 5 : 0;
  const gapPenalty = candidate.showInfoGap && candidate.infoLines.length === 0 ? -50 : 0;
  const fullLaneBonus = !pending && candidate.lanes.length === laneTotal ? 20 : 0;
  const fullInfoBonus = candidate.infoLines.length === infoTotal && infoTotal > 0 ? 10 : 0;
  const pendingInfoScore = pending ? candidate.infoLines.length * 80 : 0;
  const pendingMissingInfoPenalty =
    pending && infoTotal > 0 && candidate.infoLines.length === 0 ? -250 : 0;
  return (
    laneScore +
    infoScore +
    rulerBonus +
    gapPenalty +
    fullLaneBonus +
    fullInfoBonus +
    pendingInfoScore +
    pendingMissingInfoPenalty
  );
}

function classifyMode(
  showRuler: boolean,
  laneCount: number,
  laneTotal: number,
  infoRenderLines: number,
  infoTotal: number,
  innerW: number,
): TransitDisplayMode {
  if (laneCount <= 1 && !showRuler && infoRenderLines === 0) {
    return 'micro';
  }
  if (infoRenderLines === 0) {
    return showRuler ? 'lanesOnly' : 'minimal';
  }
  if (showRuler && laneCount === laneTotal && infoRenderLines >= infoTotal && innerW >= 24) {
    return 'full';
  }
  if (showRuler && innerW >= 18) {
    return 'compact';
  }
  if (innerW >= 12) {
    return 'narrow';
  }
  return 'minimal';
}

/**
 * Mode-only layout router (legacy export) — defers row allocation to `resolveBodyPlan`.
 */
export function layout(
  width: number,
  height: number,
  laneCount: number,
  infoLineCount: number,
): TransitLayoutPlan {
  const placeholderLanes: TreePanelLane[] = Array.from({ length: laneCount }, (_, index) => ({
    branch: `lane-${index}`,
    rail: '○━▶',
    color: '#ffffff',
  }));
  const placeholderInfo = Array.from({ length: infoLineCount }, () => 'placeholder');
  const { mode, infoLayout, tagPadding } = resolveBodyPlan(
    width,
    height,
    placeholderLanes,
    placeholderInfo,
    false,
  );
  return { mode, infoLayout, tagPadding };
}

/** Full body plan: mode plus row-accurate ruler/lane/info slices. */
export function resolveBodyPlan(
  width: number,
  height: number,
  lanes: readonly TreePanelLane[],
  info: readonly string[],
  pending: boolean,
): TransitBodyPlan {
  const innerW = contentInnerWidth(width);
  const innerH = contentInnerHeight(height);
  const tagPadding = tagPaddingForWidth(innerW);
  const infoLayout = infoLayoutForWidth(innerW);

  if (innerH < 1 || innerW < 8) {
    const selected = lanes.find((lane) => lane.selected) ?? lanes[0];
    return {
      mode: 'micro',
      infoLayout,
      tagPadding,
      showRuler: false,
      lanes: selected === undefined ? [] : [selected],
      infoLines: [],
      showInfoGap: false,
      infoRenderLayout: infoLayout,
    };
  }

  const laneTotal = lanes.length;
  const infoTotal = info.length;
  let best: BodyCandidate | null = null;

  for (const showRuler of innerW >= 18 ? [true, false] : [false]) {
    const rulerRows = showRuler ? 1 : 0;
    const bodyRows = innerH - rulerRows;
    if (bodyRows <= 0) {
      continue;
    }

    for (let laneRows = Math.min(laneTotal, bodyRows); laneRows >= 1; laneRows -= 1) {
      const visibleLanes = windowLanes(lanes, laneRows);
      const afterLanes = bodyRows - laneRows;

      const lanesOnly: Omit<BodyCandidate, 'score'> = {
        mode: showRuler ? 'lanesOnly' : 'minimal',
        showRuler,
        lanes: visibleLanes,
        infoLines: [],
        showInfoGap: false,
        infoRenderLayout: infoLayout,
      };
      const lanesOnlyScore = scoreBodyCandidate(lanesOnly, laneTotal, infoTotal, pending, innerW);
      if (best === null || lanesOnlyScore > best.score) {
        best = { ...lanesOnly, score: lanesOnlyScore };
      }

      if (afterLanes <= 0 || infoTotal === 0) {
        continue;
      }

      for (const withGap of afterLanes >= 2 ? [true, false] : [false]) {
        const gapRows = withGap ? 1 : 0;
        const infoBudget = afterLanes - gapRows;
        if (infoBudget <= 0) {
          continue;
        }
        const infoLines = fitInfoSourceLines(
          info,
          infoLayoutForBudget(innerW, infoBudget),
          pending,
          infoBudget,
          innerW,
        );
        if (infoLines.length === 0) {
          continue;
        }
        const renderLayout = infoLayoutForBudget(innerW, infoBudget);
        const infoRenderLines = countInfoRenderLines(infoLines, renderLayout, pending, innerW);
        const used = rulerRows + laneRows + gapRows + infoRenderLines;
        if (used > innerH) {
          continue;
        }
        const candidate: Omit<BodyCandidate, 'score'> = {
          mode: classifyMode(
            showRuler,
            laneRows,
            laneTotal,
            infoRenderLines,
            countInfoRenderLines(info, infoLayout, pending, innerW),
            innerW,
          ),
          showRuler,
          lanes: visibleLanes,
          infoLines,
          showInfoGap: withGap,
          infoRenderLayout: renderLayout,
        };
        const score = scoreBodyCandidate(candidate, laneTotal, infoTotal, pending, innerW);
        if (best === null || score > best.score) {
          best = { ...candidate, score };
        }
      }
    }
  }

  if (best === null) {
    const selected = lanes.find((lane) => lane.selected) ?? lanes[0];
    return {
      mode: 'micro',
      infoLayout,
      tagPadding,
      showRuler: false,
      lanes: selected === undefined ? [] : [selected],
      infoLines: [],
      showInfoGap: false,
      infoRenderLayout: infoLayout,
    };
  }

  return {
    mode: best.mode,
    infoLayout,
    tagPadding,
    showRuler: best.showRuler,
    lanes: best.lanes,
    infoLines: best.infoLines,
    showInfoGap: best.showInfoGap,
    infoRenderLayout: best.infoRenderLayout,
  };
}

/** Clip oldest ruler labels from the left; never append an ellipsis. */
function fitRuler(ruler: string, innerW: number): string {
  if (innerW <= 0) {
    return '';
  }
  if (ruler.length <= innerW) {
    return ruler;
  }
  return ruler.slice(ruler.length - innerW);
}

function LaneRow({
  lane,
  innerW,
  lanes,
  tagPadding,
}: {
  readonly lane: TreePanelLane;
  readonly innerW: number;
  readonly lanes: readonly TreePanelLane[];
  readonly tagPadding: TransitTagPadding;
}): React.JSX.Element {
  const theme = useTheme();
  const { rail, tag } = laneRowGeometry(lane, innerW, lanes, tagPadding);

  return (
    <Box flexShrink={0}>
      <Text wrap="truncate">
        <Text color={lane.color}>{rail}</Text>
        {' '.repeat(TAG_GAP)}
        <Text
          color={lane.color}
          bold={lane.selected === true}
          {...(lane.selected === true ? { backgroundColor: theme.panelSelectedBg } : {})}
        >
          {tag}
        </Text>
      </Text>
    </Box>
  );
}

function InfoSection({
  lines,
  pending,
  infoLayout,
}: {
  readonly lines: readonly string[];
  readonly pending: boolean;
  readonly infoLayout: TransitInfoLayout;
}): React.JSX.Element {
  const theme = useTheme();
  if (lines.length === 0) {
    return <Box flexShrink={0} />;
  }

  const meta = !pending ? parseInfoMetaLine(lines[0] ?? '') : null;
  const messageLines = meta === null ? lines : lines.slice(1);

  if (meta !== null && infoLayout === 'stacked') {
    return (
      <Box flexDirection="column" flexShrink={0}>
        <Box flexShrink={0}>
          <Text color={theme.text}>{meta.hash}</Text>
        </Box>
        <Box flexShrink={0}>
          <Text color={theme.heading}>{meta.branch}</Text>
        </Box>
        <Box flexShrink={0}>
          <Text dimColor>{meta.age}</Text>
        </Box>
        {messageLines.map((line, index) => (
          // biome-ignore lint/suspicious/noArrayIndexKey: fixture info lines are position-keyed slices.
          <Box key={`info-msg-${index}`} flexShrink={0}>
            <Text dimColor wrap="wrap">
              {line.length > 0 ? line : ' '}
            </Text>
          </Box>
        ))}
      </Box>
    );
  }

  if (meta !== null) {
    return (
      <Box flexDirection="column" flexShrink={0}>
        <Box flexShrink={0}>
          <Text wrap="wrap">
            <Text color={theme.text}>{meta.hash}</Text>
            <Text dimColor>{' · '}</Text>
            <Text color={theme.heading}>{meta.branch}</Text>
            <Text dimColor>{` · ${meta.age}`}</Text>
          </Text>
        </Box>
        {messageLines.map((line, index) => (
          // biome-ignore lint/suspicious/noArrayIndexKey: fixture info lines are position-keyed slices.
          <Box key={`info-msg-${index}`} flexShrink={0}>
            <Text dimColor wrap="wrap">
              {line.length > 0 ? line : ' '}
            </Text>
          </Box>
        ))}
      </Box>
    );
  }

  return (
    <Box flexDirection="column" flexShrink={0}>
      {lines.map((line, index) => (
        // biome-ignore lint/suspicious/noArrayIndexKey: fixture info lines are position-keyed slices.
        <Box key={`info-${index}`} flexShrink={0}>
          <Text
            dimColor={index > 0}
            {...(pending && index === 0 ? { color: theme.heading } : {})}
            wrap={pending ? 'truncate' : 'wrap'}
          >
            {line.length > 0 ? line : ' '}
          </Text>
        </Box>
      ))}
    </Box>
  );
}

function TransitBody({
  data,
  width,
  height,
}: {
  readonly data: TreePanelData;
  readonly width: number;
  readonly height: number;
}): React.JSX.Element {
  const innerW = contentInnerWidth(width);
  const theme = useTheme();
  if (data.status === 'error') {
    return <Text color={theme.error}>{`error: ${data.error ?? 'unknown'} (r to retry)`}</Text>;
  }
  if (data.status === 'loading' && data.lanes.length === 0) {
    return <Text dimColor>loading...</Text>;
  }
  if (data.lanes.length === 0) {
    return <Text dimColor>no branches</Text>;
  }

  const plan = resolveBodyPlan(width, height, data.lanes, data.info, data.pending === true);
  const { mode, tagPadding, showRuler, lanes, infoLines, showInfoGap, infoRenderLayout } = plan;

  if (mode === 'micro' && lanes.length === 0) {
    return (
      <Box flexShrink={0}>
        <Text dimColor>Git Tree</Text>
      </Box>
    );
  }

  return (
    <Box flexDirection="column" flexShrink={0} overflow="hidden">
      {showRuler ? (
        <Box flexShrink={0}>
          <Text dimColor wrap="truncate">
            {fitRuler(data.ruler, innerW)}
          </Text>
        </Box>
      ) : null}
      {lanes.map((lane) => (
        <LaneRow
          key={lane.branch}
          lane={lane}
          innerW={innerW}
          lanes={lanes}
          tagPadding={tagPadding}
        />
      ))}
      {showInfoGap ? <Box flexShrink={0} height={1} /> : null}
      {infoLines.length > 0 ? (
        <InfoSection
          lines={infoLines}
          pending={data.pending === true}
          infoLayout={infoRenderLayout}
        />
      ) : null}
    </Box>
  );
}

export const TreePanel = memo(function TreePanel({
  width,
  height,
  focused,
  data,
}: TreePanelProps): React.JSX.Element {
  const padding = paneHorizontalPaddingForWidth(width);
  return (
    <Pane
      title={PANEL_TITLE}
      focused={focused}
      paddingLeft={padding.paddingLeft}
      paddingRight={padding.paddingRight}
    >
      <TransitBody data={data} width={width} height={height} />
    </Pane>
  );
});
