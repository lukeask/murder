import type { Rect } from '../terminal/geometry.js';
import type {
  CellSize,
  PaneAllocation,
  PaneDenial,
  PaneDenialReason,
  PaneId,
  PaneLayoutInput,
  PaneLayoutPlan,
  PanePresentation,
  PaneRegion,
  PaneRegionPlan,
  PaneRequest,
  PaneSizing,
} from './paneLayoutTypes.js';

const REGIONS: readonly PaneRegion[] = ['leftAligned', 'centerStage', 'rightAligned'];

type Axis = 'width' | 'height';

type NormalizedRequest = PaneRequest & {
  readonly sizing: PaneSizing;
};

type RegionMeasure = {
  readonly min: CellSize;
  readonly preferred: CellSize;
};

type LayoutFailure = {
  readonly reason: PaneDenialReason;
  readonly detail: string;
  readonly regions: readonly PaneRegion[];
};

type LayoutAttempt =
  | {
      readonly ok: true;
      readonly allocations: readonly PaneAllocation[];
      readonly regions: Readonly<Record<PaneRegion, PaneRegionPlan>>;
    }
  | { readonly ok: false; readonly failure: LayoutFailure };

type AxisSegment = {
  readonly key: string;
  readonly min: number;
  readonly preferred: number;
  readonly fillWeight: number;
};

type AxisAllocation = Readonly<Record<string, number>>;

type GridCell<T> = {
  readonly item: T;
  readonly row: number;
  readonly column: number;
};

type GridLayout = {
  readonly allocations: readonly PaneAllocation[];
  readonly measure: RegionMeasure;
};

type RowPattern = readonly number[];

function cellCount(value: number): number {
  if (!Number.isFinite(value)) {
    return 0;
  }
  return Math.max(0, Math.floor(value));
}

function positiveCellCount(value: number): number {
  return Math.max(1, cellCount(value));
}

function normalizeSize(size: CellSize): CellSize {
  return {
    width: cellCount(size.width),
    height: cellCount(size.height),
  };
}

function normalizeSizing(sizing: PaneSizing): PaneSizing {
  const min = {
    width: positiveCellCount(sizing.min.width),
    height: positiveCellCount(sizing.min.height),
  };
  return {
    min,
    preferred: {
      width: Math.max(min.width, positiveCellCount(sizing.preferred.width)),
      height: Math.max(min.height, positiveCellCount(sizing.preferred.height)),
    },
  };
}

function normalizeRequest(request: PaneRequest): NormalizedRequest {
  return {
    ...request,
    reapPriority: cellCount(request.reapPriority),
    orderKey: cellCount(request.orderKey),
    sizing: normalizeSizing(request.sizing),
  };
}

function focusedPriority(request: NormalizedRequest, focusedPaneId: PaneId | undefined): number {
  if (request.id === focusedPaneId) {
    return Math.min(request.reapPriority, 1);
  }
  return request.reapPriority;
}

function byLayoutOrder(a: PaneRequest, b: PaneRequest): number {
  return a.orderKey - b.orderKey || a.id.localeCompare(b.id);
}

function groupRequests(
  requests: readonly NormalizedRequest[],
): Readonly<Record<PaneRegion, readonly NormalizedRequest[]>> {
  return {
    leftAligned: requests.filter((request) => request.region === 'leftAligned').sort(byLayoutOrder),
    centerStage: requests.filter((request) => request.region === 'centerStage').sort(byLayoutOrder),
    rightAligned: requests
      .filter((request) => request.region === 'rightAligned')
      .sort(byLayoutOrder),
  };
}

function activeRegions(
  groups: Readonly<Record<PaneRegion, readonly NormalizedRequest[]>>,
): PaneRegion[] {
  return REGIONS.filter((region) => groups[region].length > 0);
}

function sum(values: readonly number[]): number {
  return values.reduce((total, value) => total + value, 0);
}

function max(values: readonly number[]): number {
  return values.length === 0 ? 0 : Math.max(...values);
}

function stackMeasure(
  requests: readonly NormalizedRequest[],
  axis: Axis,
  gap: number,
): RegionMeasure {
  if (requests.length === 0) {
    return { min: { width: 0, height: 0 }, preferred: { width: 0, height: 0 } };
  }
  const gapTotal = gap * Math.max(0, requests.length - 1);
  if (axis === 'height') {
    return {
      min: {
        width: max(requests.map((request) => request.sizing.min.width)),
        height: sum(requests.map((request) => request.sizing.min.height)) + gapTotal,
      },
      preferred: {
        width: max(requests.map((request) => request.sizing.preferred.width)),
        height: sum(requests.map((request) => request.sizing.preferred.height)) + gapTotal,
      },
    };
  }
  return {
    min: {
      width: sum(requests.map((request) => request.sizing.min.width)) + gapTotal,
      height: max(requests.map((request) => request.sizing.min.height)),
    },
    preferred: {
      width: sum(requests.map((request) => request.sizing.preferred.width)) + gapTotal,
      height: max(requests.map((request) => request.sizing.preferred.height)),
    },
  };
}

function chunkRows<T>(items: readonly T[], columns: number): readonly (readonly T[])[] {
  const safeColumns = Math.max(1, columns);
  const rows: T[][] = [];
  for (let i = 0; i < items.length; i += safeColumns) {
    rows.push(items.slice(i, i + safeColumns));
  }
  return rows;
}

function transcriptMaxColumns(count: number, orientation: PaneLayoutInput['orientation']): number {
  if (count <= 1 || orientation === 'portrait') {
    return 1;
  }
  return Math.min(count, 3);
}

function rowPatternKey(pattern: RowPattern): string {
  return pattern.join(',');
}

function balancedRowPatterns(count: number, maxColumns: number): readonly RowPattern[] {
  if (count <= 0) {
    return [];
  }
  const safeMaxColumns = Math.max(1, Math.min(maxColumns, count));
  const patterns: RowPattern[] = [];
  const seen = new Set<string>();
  for (let rowCount = 1; rowCount <= count; rowCount += 1) {
    const base = Math.floor(count / rowCount);
    const remainder = count % rowCount;
    if (base === 0 || base + (remainder > 0 ? 1 : 0) > safeMaxColumns) {
      continue;
    }
    const pattern = Array.from({ length: rowCount }, (_, row) => base + (row < remainder ? 1 : 0));
    const key = rowPatternKey(pattern);
    if (!seen.has(key)) {
      seen.add(key);
      patterns.push(pattern);
    }
  }
  return patterns;
}

function rowPatternMeasure(
  requests: readonly NormalizedRequest[],
  pattern: RowPattern,
  gap: number,
): RegionMeasure {
  let cursor = 0;
  const rowMeasures = pattern.map((columns) => {
    const row = requests.slice(cursor, cursor + columns);
    cursor += columns;
    const gapTotal = gap * Math.max(0, row.length - 1);
    return {
      min: {
        width: sum(row.map((request) => request.sizing.min.width)) + gapTotal,
        height: max(row.map((request) => request.sizing.min.height)),
      },
      preferred: {
        width: sum(row.map((request) => request.sizing.preferred.width)) + gapTotal,
        height: max(row.map((request) => request.sizing.preferred.height)),
      },
    };
  });
  const rowGapTotal = gap * Math.max(0, rowMeasures.length - 1);
  return {
    min: {
      width: max(rowMeasures.map((measure) => measure.min.width)),
      height: sum(rowMeasures.map((measure) => measure.min.height)) + rowGapTotal,
    },
    preferred: {
      width: max(rowMeasures.map((measure) => measure.preferred.width)),
      height: sum(rowMeasures.map((measure) => measure.preferred.height)) + rowGapTotal,
    },
  };
}

function adaptiveRowsMeasure(
  requests: readonly NormalizedRequest[],
  maxColumns: number,
  gap: number,
): RegionMeasure {
  if (requests.length === 0) {
    return { min: { width: 0, height: 0 }, preferred: { width: 0, height: 0 } };
  }
  const measures = balancedRowPatterns(requests.length, maxColumns).map((pattern) =>
    rowPatternMeasure(requests, pattern, gap),
  );
  return {
    min: {
      width: Math.min(...measures.map((measure) => measure.min.width)),
      height: Math.min(...measures.map((measure) => measure.min.height)),
    },
    preferred: {
      width: Math.min(...measures.map((measure) => measure.preferred.width)),
      height: Math.min(...measures.map((measure) => measure.preferred.height)),
    },
  };
}

function gridMeasure(
  requests: readonly NormalizedRequest[],
  columns: number,
  gap: number,
): RegionMeasure {
  if (requests.length === 0) {
    return { min: { width: 0, height: 0 }, preferred: { width: 0, height: 0 } };
  }
  const rows = chunkRows(requests, columns);
  const columnCount = Math.max(1, Math.min(columns, requests.length));
  const columnRequests = Array.from({ length: columnCount }, (_, column) =>
    requests.filter((_, index) => index % columnCount === column),
  );
  const minColumnWidths = columnRequests.map((items) =>
    max(items.map((request) => request.sizing.min.width)),
  );
  const preferredColumnWidths = columnRequests.map((items) =>
    max(items.map((request) => request.sizing.preferred.width)),
  );
  const minRowHeights = rows.map((row) => max(row.map((request) => request.sizing.min.height)));
  const preferredRowHeights = rows.map((row) =>
    max(row.map((request) => request.sizing.preferred.height)),
  );
  return {
    min: {
      width: sum(minColumnWidths) + gap * Math.max(0, columnCount - 1),
      height: sum(minRowHeights) + gap * Math.max(0, rows.length - 1),
    },
    preferred: {
      width: sum(preferredColumnWidths) + gap * Math.max(0, columnCount - 1),
      height: sum(preferredRowHeights) + gap * Math.max(0, rows.length - 1),
    },
  };
}

function centerMeasure(
  requests: readonly NormalizedRequest[],
  orientation: PaneLayoutInput['orientation'],
  gap: number,
): RegionMeasure {
  if (requests.length === 0) {
    return { min: { width: 0, height: 0 }, preferred: { width: 0, height: 0 } };
  }
  if (orientation === 'portrait') {
    return stackMeasure(requests, 'height', gap);
  }

  const docs = requests.filter((request) => request.kind === 'stageDoc');
  const nonDocs = requests.filter((request) => request.kind !== 'stageDoc');
  if (docs.length === 0) {
    return adaptiveRowsMeasure(nonDocs, transcriptMaxColumns(nonDocs.length, orientation), gap);
  }
  if (nonDocs.length === 0) {
    return stackMeasure(docs, 'height', gap);
  }

  const docMeasure = stackMeasure(docs, 'height', gap);
  const transcriptMeasure = adaptiveRowsMeasure(
    nonDocs,
    transcriptMaxColumns(nonDocs.length, orientation),
    gap,
  );
  return {
    min: {
      width: docMeasure.min.width + gap + transcriptMeasure.min.width,
      height: Math.max(docMeasure.min.height, transcriptMeasure.min.height),
    },
    preferred: {
      width: docMeasure.preferred.width + gap + transcriptMeasure.preferred.width,
      height: Math.max(docMeasure.preferred.height, transcriptMeasure.preferred.height),
    },
  };
}

function sidePortraitMeasure(
  requests: readonly NormalizedRequest[],
  availableWidth: number,
  gap: number,
): RegionMeasure {
  if (requests.length === 0) {
    return { min: { width: 0, height: 0 }, preferred: { width: 0, height: 0 } };
  }

  for (let columns = requests.length; columns >= 1; columns -= 1) {
    const measure = gridMeasure(requests, columns, gap);
    if (measure.min.width <= availableWidth) {
      return measure;
    }
  }

  return gridMeasure(requests, 1, gap);
}

function regionMeasure(
  region: PaneRegion,
  requests: readonly NormalizedRequest[],
  orientation: PaneLayoutInput['orientation'],
  gap: number,
  crossAxisAvailable: number,
): RegionMeasure {
  if (region === 'centerStage') {
    return centerMeasure(requests, orientation, gap);
  }
  if (orientation === 'portrait') {
    return sidePortraitMeasure(requests, crossAxisAvailable, gap);
  }
  return stackMeasure(requests, orientation === 'landscape' ? 'height' : 'width', gap);
}

function allocateAxis(total: number, segments: readonly AxisSegment[]): AxisAllocation | null {
  const minTotal = sum(segments.map((segment) => segment.min));
  if (minTotal > total) {
    return null;
  }

  const values = new Map<string, number>();
  for (const segment of segments) {
    values.set(segment.key, segment.min);
  }

  let remaining = total - minTotal;
  const preferredNeeds = segments.map((segment) => Math.max(0, segment.preferred - segment.min));
  const preferredNeedTotal = sum(preferredNeeds);
  if (preferredNeedTotal > 0 && remaining > 0) {
    const used = Math.min(remaining, preferredNeedTotal);
    let distributed = 0;
    segments.forEach((segment, index) => {
      const need = preferredNeeds[index] ?? 0;
      const share = Math.min(need, Math.floor((used * need) / preferredNeedTotal));
      values.set(segment.key, (values.get(segment.key) ?? 0) + share);
      distributed += share;
    });
    let remainder = used - distributed;
    for (const segment of segments) {
      if (remainder <= 0) {
        break;
      }
      const current = values.get(segment.key) ?? 0;
      const need = Math.max(0, segment.preferred - current);
      if (need > 0) {
        values.set(segment.key, current + 1);
        remainder -= 1;
      }
    }
    remaining -= used;
  }

  if (remaining > 0) {
    const fillTotal = sum(segments.map((segment) => Math.max(0, segment.fillWeight)));
    const fillSegments =
      fillTotal > 0 ? segments : segments.map((segment) => ({ ...segment, fillWeight: 1 }));
    const safeFillTotal = fillTotal > 0 ? fillTotal : fillSegments.length;
    let distributed = 0;
    for (const segment of fillSegments) {
      const weight = Math.max(0, segment.fillWeight);
      const share = Math.floor((remaining * weight) / safeFillTotal);
      values.set(segment.key, (values.get(segment.key) ?? 0) + share);
      distributed += share;
    }
    let remainder = remaining - distributed;
    for (const segment of fillSegments) {
      if (remainder <= 0) {
        break;
      }
      if (segment.fillWeight > 0) {
        values.set(segment.key, (values.get(segment.key) ?? 0) + 1);
        remainder -= 1;
      }
    }
  }

  return Object.fromEntries(values.entries());
}

function presentationFor(
  request: NormalizedRequest,
  rect: Rect,
  focusedPaneId: PaneId | undefined,
): PanePresentation {
  return {
    width: rect.width,
    height: rect.height,
    focused: request.id === focusedPaneId,
  };
}

function allocationFor(
  request: NormalizedRequest,
  rect: Rect,
  focusedPaneId: PaneId | undefined,
): PaneAllocation {
  return {
    request,
    region: request.region,
    rect,
    presentation: presentationFor(request, rect, focusedPaneId),
  };
}

function layoutStack(
  requests: readonly NormalizedRequest[],
  rect: Rect,
  axis: Axis,
  gap: number,
  focusedPaneId: PaneId | undefined,
): readonly PaneAllocation[] | null {
  if (requests.length === 0) {
    return [];
  }
  const available = axis === 'height' ? rect.height : rect.width;
  const gapTotal = gap * Math.max(0, requests.length - 1);
  const segments = requests.map((request) => ({
    key: request.id,
    min: axis === 'height' ? request.sizing.min.height : request.sizing.min.width,
    preferred: axis === 'height' ? request.sizing.preferred.height : request.sizing.preferred.width,
    fillWeight: 1,
  }));
  const axisValues = allocateAxis(available - gapTotal, segments);
  if (axisValues === null) {
    return null;
  }

  let cursor = axis === 'height' ? rect.y : rect.x;
  const allocations: PaneAllocation[] = [];
  for (const request of requests) {
    const size = axisValues[request.id] ?? 0;
    const paneRect =
      axis === 'height'
        ? { x: rect.x, y: cursor, width: rect.width, height: size }
        : { x: cursor, y: rect.y, width: size, height: rect.height };
    if (paneRect.width < request.sizing.min.width || paneRect.height < request.sizing.min.height) {
      return null;
    }
    allocations.push(allocationFor(request, paneRect, focusedPaneId));
    cursor += size + gap;
  }
  return allocations;
}

function gridCells<T>(items: readonly T[], columns: number): readonly GridCell<T>[] {
  const safeColumns = Math.max(1, columns);
  return items.map((item, index) => ({
    item,
    row: Math.floor(index / safeColumns),
    column: index % safeColumns,
  }));
}

function layoutGrid(
  requests: readonly NormalizedRequest[],
  rect: Rect,
  columns: number,
  gap: number,
  focusedPaneId: PaneId | undefined,
): GridLayout | null {
  if (requests.length === 0) {
    return {
      allocations: [],
      measure: { min: { width: 0, height: 0 }, preferred: { width: 0, height: 0 } },
    };
  }
  const columnCount = Math.max(1, Math.min(columns, requests.length));
  const cells = gridCells(requests, columnCount);
  const rowCount = max(cells.map((cell) => cell.row)) + 1;
  const columnSegments = Array.from({ length: columnCount }, (_, column) => {
    const columnItems = cells.filter((cell) => cell.column === column).map((cell) => cell.item);
    return {
      key: `column:${column}`,
      min: max(columnItems.map((request) => request.sizing.min.width)),
      preferred: max(columnItems.map((request) => request.sizing.preferred.width)),
      fillWeight: 1,
    };
  });
  const rowSegments = Array.from({ length: rowCount }, (_, row) => {
    const rowItems = cells.filter((cell) => cell.row === row).map((cell) => cell.item);
    return {
      key: `row:${row}`,
      min: max(rowItems.map((request) => request.sizing.min.height)),
      preferred: max(rowItems.map((request) => request.sizing.preferred.height)),
      fillWeight: 1,
    };
  });
  const columnSizes = allocateAxis(rect.width - gap * Math.max(0, columnCount - 1), columnSegments);
  const rowSizes = allocateAxis(rect.height - gap * Math.max(0, rowCount - 1), rowSegments);
  if (columnSizes === null || rowSizes === null) {
    return null;
  }

  const columnStarts: number[] = [];
  let x = rect.x;
  for (let column = 0; column < columnCount; column += 1) {
    columnStarts.push(x);
    x += (columnSizes[`column:${column}`] ?? 0) + gap;
  }

  const rowStarts: number[] = [];
  let y = rect.y;
  for (let row = 0; row < rowCount; row += 1) {
    rowStarts.push(y);
    y += (rowSizes[`row:${row}`] ?? 0) + gap;
  }

  const allocations: PaneAllocation[] = [];
  for (const cell of cells) {
    const width = columnSizes[`column:${cell.column}`] ?? 0;
    const height = rowSizes[`row:${cell.row}`] ?? 0;
    const paneRect = {
      x: columnStarts[cell.column] ?? rect.x,
      y: rowStarts[cell.row] ?? rect.y,
      width,
      height,
    };
    if (width < cell.item.sizing.min.width || height < cell.item.sizing.min.height) {
      return null;
    }
    allocations.push(allocationFor(cell.item, paneRect, focusedPaneId));
  }
  return {
    allocations,
    measure: gridMeasure(requests, columnCount, gap),
  };
}

function layoutBestGrid(
  requests: readonly NormalizedRequest[],
  rect: Rect,
  maxColumns: number,
  gap: number,
  focusedPaneId: PaneId | undefined,
): GridLayout | null {
  let best: GridLayout | null = null;
  for (let columns = Math.max(1, maxColumns); columns >= 1; columns -= 1) {
    const attempt = layoutGrid(requests, rect, columns, gap, focusedPaneId);
    if (attempt !== null) {
      best = attempt;
      break;
    }
  }
  return best;
}

function layoutRowsPattern(
  requests: readonly NormalizedRequest[],
  rect: Rect,
  pattern: RowPattern,
  gap: number,
  focusedPaneId: PaneId | undefined,
): GridLayout | null {
  if (requests.length === 0) {
    return {
      allocations: [],
      measure: { min: { width: 0, height: 0 }, preferred: { width: 0, height: 0 } },
    };
  }

  const rowSegments: AxisSegment[] = [];
  let requestCursor = 0;
  for (let row = 0; row < pattern.length; row += 1) {
    const columns = pattern[row] ?? 0;
    const rowItems = requests.slice(requestCursor, requestCursor + columns);
    requestCursor += columns;
    rowSegments.push({
      key: `row:${row}`,
      min: max(rowItems.map((request) => request.sizing.min.height)),
      preferred: max(rowItems.map((request) => request.sizing.preferred.height)),
      fillWeight: 1,
    });
  }

  const rowSizes = allocateAxis(rect.height - gap * Math.max(0, pattern.length - 1), rowSegments);
  if (rowSizes === null) {
    return null;
  }

  const allocations: PaneAllocation[] = [];
  let y = rect.y;
  requestCursor = 0;
  for (let row = 0; row < pattern.length; row += 1) {
    const columns = pattern[row] ?? 0;
    const rowItems = requests.slice(requestCursor, requestCursor + columns);
    requestCursor += columns;
    const rowHeight = rowSizes[`row:${row}`] ?? 0;
    const columnSegments = rowItems.map((request, column) => ({
      key: `column:${row}:${column}`,
      min: request.sizing.min.width,
      preferred: request.sizing.preferred.width,
      fillWeight: 1,
    }));
    const columnSizes = allocateAxis(
      rect.width - gap * Math.max(0, rowItems.length - 1),
      columnSegments,
    );
    if (columnSizes === null) {
      return null;
    }

    let x = rect.x;
    for (let column = 0; column < rowItems.length; column += 1) {
      const request = rowItems[column];
      if (request === undefined) {
        continue;
      }
      const width = columnSizes[`column:${row}:${column}`] ?? 0;
      if (width < request.sizing.min.width || rowHeight < request.sizing.min.height) {
        return null;
      }
      allocations.push(allocationFor(request, { x, y, width, height: rowHeight }, focusedPaneId));
      x += width + gap;
    }
    y += rowHeight + gap;
  }

  return {
    allocations,
    measure: rowPatternMeasure(requests, pattern, gap),
  };
}

function transcriptLayoutScore(layout: GridLayout): number {
  if (layout.allocations.length === 0) {
    return 0;
  }

  const fitRatios = layout.allocations.map((allocation) => {
    const preferred = allocation.request.sizing.preferred;
    return Math.min(
      allocation.rect.width / preferred.width,
      allocation.rect.height / preferred.height,
      1,
    );
  });
  const minFit = Math.min(...fitRatios);
  const avgFit = sum(fitRatios) / fitRatios.length;
  const areas = layout.allocations.map(
    (allocation) => allocation.rect.width * allocation.rect.height,
  );
  const avgArea = sum(areas) / areas.length;
  const areaImbalance = avgArea <= 0 ? 0 : (Math.max(...areas) - Math.min(...areas)) / avgArea;
  const aspectPenalty =
    sum(
      layout.allocations.map((allocation) => {
        const preferred = allocation.request.sizing.preferred;
        const target = preferred.width / preferred.height;
        const actual = allocation.rect.width / allocation.rect.height;
        return Math.abs(Math.log(Math.max(actual, 0.01) / Math.max(target, 0.01)));
      }),
    ) / layout.allocations.length;

  return minFit * 1000 + avgFit * 100 - areaImbalance * 20 - aspectPenalty * 8;
}

function layoutAdaptiveTranscriptRows(
  requests: readonly NormalizedRequest[],
  rect: Rect,
  maxColumns: number,
  gap: number,
  focusedPaneId: PaneId | undefined,
): readonly PaneAllocation[] | null {
  let best: GridLayout | null = null;
  let bestScore = Number.NEGATIVE_INFINITY;
  for (const pattern of balancedRowPatterns(requests.length, maxColumns)) {
    const attempt = layoutRowsPattern(requests, rect, pattern, gap, focusedPaneId);
    if (attempt === null) {
      continue;
    }
    const score = transcriptLayoutScore(attempt);
    if (score > bestScore) {
      best = attempt;
      bestScore = score;
    }
  }
  return best?.allocations ?? null;
}

function layoutCenter(
  requests: readonly NormalizedRequest[],
  rect: Rect,
  orientation: PaneLayoutInput['orientation'],
  gap: number,
  focusedPaneId: PaneId | undefined,
): readonly PaneAllocation[] | null {
  if (orientation === 'portrait') {
    return layoutStack(requests, rect, 'height', gap, focusedPaneId);
  }

  const docs = requests.filter((request) => request.kind === 'stageDoc');
  const nonDocs = requests.filter((request) => request.kind !== 'stageDoc');
  if (docs.length === 0) {
    return layoutAdaptiveTranscriptRows(
      nonDocs,
      rect,
      transcriptMaxColumns(nonDocs.length, orientation),
      gap,
      focusedPaneId,
    );
  }
  if (nonDocs.length === 0) {
    return layoutStack(docs, rect, 'height', gap, focusedPaneId);
  }

  const docMeasure = stackMeasure(docs, 'height', gap);
  const transcriptMeasure = adaptiveRowsMeasure(
    nonDocs,
    transcriptMaxColumns(nonDocs.length, orientation),
    gap,
  );
  const split = allocateAxis(rect.width - gap, [
    {
      key: 'docs',
      min: docMeasure.min.width,
      preferred: docMeasure.preferred.width,
      fillWeight: 1,
    },
    {
      key: 'transcripts',
      min: transcriptMeasure.min.width,
      preferred: transcriptMeasure.preferred.width,
      fillWeight: nonDocs.length >= 4 ? 2 : 1,
    },
  ]);
  if (split === null) {
    return null;
  }
  const centerSplit: AxisAllocation & {
    readonly docs?: number;
    readonly transcripts?: number;
  } = split;
  const docWidth = centerSplit.docs ?? 0;
  const transcriptWidth = centerSplit.transcripts ?? 0;
  const docRect = { x: rect.x, y: rect.y, width: docWidth, height: rect.height };
  const transcriptRect = {
    x: rect.x + docWidth + gap,
    y: rect.y,
    width: transcriptWidth,
    height: rect.height,
  };
  const docAllocations = layoutStack(docs, docRect, 'height', gap, focusedPaneId);
  const transcriptAllocations = layoutAdaptiveTranscriptRows(
    nonDocs,
    transcriptRect,
    transcriptMaxColumns(nonDocs.length, orientation),
    gap,
    focusedPaneId,
  );
  if (docAllocations === null || transcriptAllocations === null) {
    return null;
  }
  return [...docAllocations, ...transcriptAllocations];
}

function layoutRegion(
  region: PaneRegion,
  requests: readonly NormalizedRequest[],
  rect: Rect,
  orientation: PaneLayoutInput['orientation'],
  gap: number,
  focusedPaneId: PaneId | undefined,
): readonly PaneAllocation[] | null {
  if (region === 'centerStage') {
    return layoutCenter(requests, rect, orientation, gap, focusedPaneId);
  }
  if (orientation === 'portrait') {
    const grid = layoutBestGrid(requests, rect, requests.length, gap, focusedPaneId);
    return grid?.allocations ?? null;
  }
  return layoutStack(
    requests,
    rect,
    orientation === 'landscape' ? 'height' : 'width',
    gap,
    focusedPaneId,
  );
}

function emptyRegionPlan(region: PaneRegion): PaneRegionPlan {
  return { region, rect: null, allocations: [] };
}

function regionPlans(
  entries: readonly [PaneRegion, Rect, readonly PaneAllocation[]][],
): Readonly<Record<PaneRegion, PaneRegionPlan>> {
  const plans: Record<PaneRegion, PaneRegionPlan> = {
    leftAligned: emptyRegionPlan('leftAligned'),
    centerStage: emptyRegionPlan('centerStage'),
    rightAligned: emptyRegionPlan('rightAligned'),
  };
  for (const [region, rect, allocations] of entries) {
    plans[region] = { region, rect, allocations };
  }
  return plans;
}

function attemptLayout(
  requests: readonly NormalizedRequest[],
  bodyRect: Rect,
  orientation: PaneLayoutInput['orientation'],
  gap: number,
  focusedPaneId: PaneId | undefined,
): LayoutAttempt {
  if (bodyRect.width <= 0 || bodyRect.height <= 0) {
    return {
      ok: false,
      failure: {
        reason: 'terminalTooSmall',
        detail: 'Terminal chrome leaves no drawable body cells for pane allocation.',
        regions: REGIONS,
      },
    };
  }

  const groups = groupRequests(requests);
  const regions = activeRegions(groups);
  if (regions.length === 0) {
    return { ok: true, allocations: [], regions: regionPlans([]) };
  }

  const primaryAxis: Axis = orientation === 'landscape' ? 'width' : 'height';
  const crossAxis: Axis = primaryAxis === 'width' ? 'height' : 'width';
  const primaryTotal = bodyRect[primaryAxis] - gap * Math.max(0, regions.length - 1);
  const crossTotal = bodyRect[crossAxis];
  const measures = new Map<PaneRegion, RegionMeasure>();
  for (const region of regions) {
    measures.set(region, regionMeasure(region, groups[region], orientation, gap, crossTotal));
  }

  const crossFailures = regions.filter((region) => {
    const measure = measures.get(region);
    return measure === undefined || measure.min[crossAxis] > crossTotal;
  });
  if (crossFailures.length > 0) {
    return {
      ok: false,
      failure: {
        reason: 'belowMinimum',
        detail: `Body ${crossAxis} is smaller than the minimum ${crossAxis} required by ${crossFailures.join(', ')}.`,
        regions: crossFailures,
      },
    };
  }

  const segments = regions.map((region) => {
    const measure = measures.get(region);
    return {
      key: region,
      min: measure?.min[primaryAxis] ?? 0,
      preferred: measure?.preferred[primaryAxis] ?? 0,
      fillWeight: region === 'centerStage' ? 1 : 0,
    };
  });
  const primarySizes = allocateAxis(primaryTotal, segments);
  if (primarySizes === null) {
    return {
      ok: false,
      failure: {
        reason: 'belowMinimum',
        detail: `Body ${primaryAxis} cannot satisfy minimum pane sizes for the active regions.`,
        regions,
      },
    };
  }

  let cursor = orientation === 'landscape' ? bodyRect.x : bodyRect.y;
  const entries: [PaneRegion, Rect, readonly PaneAllocation[]][] = [];
  const allocations: PaneAllocation[] = [];
  for (const region of regions) {
    const primarySize = primarySizes[region] ?? 0;
    const rect =
      orientation === 'landscape'
        ? { x: cursor, y: bodyRect.y, width: primarySize, height: bodyRect.height }
        : { x: bodyRect.x, y: cursor, width: bodyRect.width, height: primarySize };
    const regionAllocations = layoutRegion(
      region,
      groups[region],
      rect,
      orientation,
      gap,
      focusedPaneId,
    );
    if (regionAllocations === null) {
      return {
        ok: false,
        failure: {
          reason: 'belowMinimum',
          detail: `${region} could not tile its panes without falling below their minimum dimensions.`,
          regions: [region],
        },
      };
    }
    entries.push([region, rect, regionAllocations]);
    allocations.push(...regionAllocations);
    cursor += primarySize + gap;
  }

  return { ok: true, allocations, regions: regionPlans(entries) };
}

function denial(request: PaneRequest, reason: PaneDenialReason, detail: string): PaneDenial {
  return { request, reason, detail };
}

function cutCandidate(
  requests: readonly NormalizedRequest[],
  failingRegions: readonly PaneRegion[],
  focusedPaneId: PaneId | undefined,
  canLayoutWithout?: (request: NormalizedRequest) => boolean,
): NormalizedRequest | null {
  const regionSet = new Set(failingRegions);
  const candidates = requests.filter(
    (request) => regionSet.has(request.region) && focusedPriority(request, focusedPaneId) > 0,
  );
  if (candidates.length === 0) {
    return null;
  }
  const byCutOrder = (a: NormalizedRequest, b: NormalizedRequest): number =>
    focusedPriority(b, focusedPaneId) - focusedPriority(a, focusedPaneId) ||
    b.orderKey - a.orderKey ||
    b.id.localeCompare(a.id);

  const sorted = [...candidates].sort(byCutOrder);
  const resolvingCandidate =
    canLayoutWithout === undefined
      ? undefined
      : sorted.find((candidate) => canLayoutWithout(candidate));
  if (resolvingCandidate !== undefined) {
    return resolvingCandidate;
  }
  return sorted[0] ?? null;
}

function canAttemptLayoutWithout(
  requests: readonly NormalizedRequest[],
  candidate: NormalizedRequest,
  bodyRect: Rect,
  orientation: PaneLayoutInput['orientation'],
  gap: number,
  focusedPaneId: PaneId | undefined,
): boolean {
  const remaining = requests.filter((request) => request.id !== candidate.id);
  return attemptLayout(remaining, bodyRect, orientation, gap, focusedPaneId).ok;
}

function byLayoutOrderForAllocation(a: PaneAllocation, b: PaneAllocation): number {
  return byLayoutOrder(a.request, b.request);
}

function sortedAllocations(allocations: readonly PaneAllocation[]): PaneAllocation[] {
  return [...allocations].sort(byLayoutOrderForAllocation);
}

function buildBodyRect(input: PaneLayoutInput): Rect {
  const terminal = normalizeSize(input.terminal);
  const topBar = cellCount(input.chrome.topBar);
  const bottomBar = cellCount(input.chrome.bottomBar);
  const chatInput = cellCount(input.chrome.chatInput);
  const bodySize =
    input.body === undefined
      ? {
          width: terminal.width,
          height: Math.max(0, terminal.height - topBar - bottomBar - chatInput),
        }
      : normalizeSize(input.body);
  const origin = input.bodyOrigin ?? { x: 0, y: topBar };
  return {
    x: cellCount(origin.x),
    y: cellCount(origin.y),
    width: bodySize.width,
    height: bodySize.height,
  };
}

function normalizedChrome(input: PaneLayoutInput): PaneLayoutPlan['chrome'] {
  return {
    topBar: cellCount(input.chrome.topBar),
    bottomBar: cellCount(input.chrome.bottomBar),
    chatInput: cellCount(input.chrome.chatInput),
  };
}

function terminalTooSmallPlan(
  input: PaneLayoutInput,
  bodyRect: Rect,
  requests: readonly NormalizedRequest[],
): PaneLayoutPlan {
  const denials = requests.map((request) =>
    denial(request, 'terminalTooSmall', 'Terminal chrome leaves no drawable body cells.'),
  );
  return {
    terminal: normalizeSize(input.terminal),
    chrome: normalizedChrome(input),
    body: { width: bodyRect.width, height: bodyRect.height },
    bodyRect,
    orientation: input.orientation,
    gap: cellCount(input.gap),
    allocations: [],
    denials,
    regions: regionPlans([]),
    stage: { docs: [], transcripts: [], other: [] },
  };
}

function stageGroup(allocations: readonly PaneAllocation[]): PaneLayoutPlan['stage'] {
  const centerAllocations = allocations.filter((allocation) => allocation.region === 'centerStage');
  return {
    docs: centerAllocations.filter((allocation) => allocation.request.kind === 'stageDoc'),
    transcripts: centerAllocations.filter(
      (allocation) => allocation.request.kind === 'stageTranscript',
    ),
    other: centerAllocations.filter(
      (allocation) =>
        allocation.request.kind !== 'stageDoc' && allocation.request.kind !== 'stageTranscript',
    ),
  };
}

export function computePaneLayout(input: PaneLayoutInput): PaneLayoutPlan {
  const gap = cellCount(input.gap);
  const bodyRect = buildBodyRect(input);
  let remaining = input.requests.map(normalizeRequest);
  const denials: PaneDenial[] = [];

  if (bodyRect.width <= 0 || bodyRect.height <= 0) {
    return terminalTooSmallPlan(input, bodyRect, remaining);
  }

  for (;;) {
    const attempt = attemptLayout(remaining, bodyRect, input.orientation, gap, input.focusedPaneId);
    if (attempt.ok) {
      const allocations = sortedAllocations(attempt.allocations);
      return {
        terminal: normalizeSize(input.terminal),
        chrome: normalizedChrome(input),
        body: { width: bodyRect.width, height: bodyRect.height },
        bodyRect,
        orientation: input.orientation,
        gap,
        allocations,
        denials,
        regions: attempt.regions,
        stage: stageGroup(allocations),
      };
    }

    const candidate = cutCandidate(
      remaining,
      attempt.failure.regions,
      input.focusedPaneId,
      (request) =>
        canAttemptLayoutWithout(
          remaining,
          request,
          bodyRect,
          input.orientation,
          gap,
          input.focusedPaneId,
        ),
    );
    if (candidate === null) {
      for (const request of remaining.filter((request) =>
        attempt.failure.regions.includes(request.region),
      )) {
        denials.push(denial(request, attempt.failure.reason, attempt.failure.detail));
      }
      remaining = remaining.filter((request) => !attempt.failure.regions.includes(request.region));
      continue;
    }

    denials.push(
      denial(
        candidate,
        'preemptedByReapPriority',
        `Cut under layout pressure; effective reap priority ${focusedPriority(
          candidate,
          input.focusedPaneId,
        )}.`,
      ),
    );
    remaining = remaining.filter((request) => request.id !== candidate.id);
  }
}
