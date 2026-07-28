import {
  type CellStyle,
  type CellSurface,
  drawBox,
  drawHorizontal,
  drawVertical,
  putCell,
  putClippedText,
} from '../render/cellSurface.js';
import type { GraphLayout } from './layout.js';
import type { EditorIssue, StageKey, Viewport } from './model.js';
import { type DirectionMask, glyphForMask, routeEdges, segmentMasks } from './routing.js';

export type ConnectLegality = 'add' | 'remove' | 'cycle' | 'invalid';

export interface PaintStyles {
  readonly edge?: CellStyle;
  readonly cycleEdge?: CellStyle;
  readonly node?: CellStyle;
  readonly selected?: CellStyle;
  readonly invalid?: CellStyle;
  readonly cycle?: CellStyle;
  readonly dependency?: CellStyle;
  readonly dependent?: CellStyle;
  readonly candidateAdd?: CellStyle;
  readonly candidateRemove?: CellStyle;
  readonly candidateIllegal?: CellStyle;
  readonly runtime?: Readonly<Record<string, CellStyle>>;
  readonly runtimeStatuses?: ReadonlyMap<string, string>;
  readonly issuesByNode?: ReadonlyMap<StageKey, readonly EditorIssue[]>;
  readonly connect?: {
    readonly target: StageKey;
    readonly candidate: StageKey;
    readonly legality: ConnectLegality;
  };
}

export function paintWorkflow(
  surface: CellSurface,
  layout: GraphLayout,
  viewport: Viewport,
  selected: StageKey | null,
  styles: PaintStyles = {},
): CellSurface {
  const translate = (x: number, y: number): [number, number] => [x - viewport.x, y - viewport.y];
  const { edges, stubs } = routeEdges(layout);
  const masks = new Map<string, { mask: DirectionMask; cycle: boolean }>();
  for (const route of edges) {
    const component = layout.graph.componentByNode.get(route.source);
    const cycle = component?.cyclic === true && component.members.includes(route.target);
    for (const [position, mask] of segmentMasks(route.points)) {
      const existing = masks.get(position);
      masks.set(position, {
        mask: (existing?.mask ?? 0) | mask,
        cycle: cycle || existing?.cycle === true,
      });
    }
  }
  for (const route of stubs) {
    for (const [position, mask] of segmentMasks(route.points)) {
      const existing = masks.get(position);
      masks.set(position, { mask: (existing?.mask ?? 0) | mask, cycle: existing?.cycle === true });
    }
  }
  for (const [position, entry] of masks) {
    const [x, y] = position.split(',').map(Number) as [number, number];
    const [sx, sy] = translate(x, y);
    putCell(
      surface,
      sx,
      sy,
      glyphForMask(entry.mask),
      entry.cycle ? (styles.cycleEdge ?? styles.edge) : styles.edge,
    );
  }
  for (const route of edges) {
    const point = route.points.at(-1);
    if (point !== undefined) {
      const [x, y] = translate(point.x, point.y);
      putCell(surface, x, y, '▶', styles.edge);
    }
  }
  for (const stub of stubs) {
    const unknown = stub.points.at(-1);
    if (unknown !== undefined) {
      const [x, y] = translate(unknown.x, unknown.y);
      putCell(surface, x, y, '?', styles.invalid ?? styles.edge);
    }
    const target = stub.points[0];
    if (target !== undefined) {
      const [x, y] = translate(target.x, target.y);
      putCell(surface, x, y, '▶', styles.invalid ?? styles.edge);
    }
  }
  const selectedDependencies = new Set(
    (selected === null ? [] : (layout.graph.incoming.get(selected) ?? [])).map(
      (edge) => edge.source,
    ),
  );
  const selectedDependents = new Set(
    (selected === null ? [] : (layout.graph.outgoing.get(selected) ?? [])).map(
      (edge) => edge.target,
    ),
  );
  for (const stage of layout.graph.nodes.values()) {
    const positioned = layout.nodes.get(stage.key);
    if (positioned === undefined) continue;
    const [x, y] = translate(positioned.rect.x, positioned.rect.y);
    const graphIssues = layout.graph.issuesByNode.get(stage.key) ?? [];
    const extraIssues = styles.issuesByNode?.get(stage.key) ?? [];
    const issues = [...graphIssues, ...extraIssues].filter(
      (issue, index, all) =>
        all.findIndex(
          (candidate) =>
            candidate.code === issue.code &&
            candidate.stageKey === issue.stageKey &&
            candidate.dependencyIndex === issue.dependencyIndex &&
            candidate.field === issue.field &&
            candidate.message === issue.message,
        ) === index,
    );
    const component = layout.graph.componentByNode.get(stage.key);
    const runtimeStatus = styles.runtimeStatuses?.get(stage.id);
    const runtimeStyle = runtimeStatus === undefined ? undefined : styles.runtime?.[runtimeStatus];
    const candidateStyle =
      styles.connect?.candidate !== stage.key
        ? undefined
        : styles.connect.legality === 'add'
          ? styles.candidateAdd
          : styles.connect.legality === 'remove'
            ? styles.candidateRemove
            : styles.candidateIllegal;
    const style =
      candidateStyle ??
      (stage.key === selected
        ? (styles.selected ?? styles.node)
        : issues.length > 0
          ? component?.cyclic === true
            ? (styles.cycle ?? styles.invalid ?? styles.node)
            : (styles.invalid ?? styles.node)
          : selectedDependencies.has(stage.key)
            ? (styles.dependency ?? styles.node)
            : selectedDependents.has(stage.key)
              ? (styles.dependent ?? styles.node)
              : (runtimeStyle ?? styles.node));
    drawBox(surface, { x, y, width: positioned.rect.width, height: positioned.rect.height }, style);
    const definition = layout.graph.workflow.stages[stage.index];
    if (definition === undefined) continue;
    const innerWidth = positioned.rect.width - 2;
    putClippedText(surface, x + 1, y, innerWidth, definition.id || '(blank)', style);
    putClippedText(surface, x + 1, y + 1, innerWidth, definition.title || '(untitled)', style);
    const harnessModel = [definition.harness, definition.model].filter(Boolean).join(' · ');
    putClippedText(surface, x + 1, y + 2, innerWidth, harnessModel || '(runtime required)', style);
    const indicators = [
      definition.dependsOn.length === 0 ? 'root' : '',
      (layout.graph.outgoing.get(stage.key) ?? []).length === 0 ? 'sink' : '',
      runtimeStatus ?? '',
      issues.length === 0 ? '' : `!${issues.length}`,
    ].filter((value) => value !== '');
    const indicatorText = indicators.join(' · ');
    const worktreeText = definition.worktree === null ? '' : `wt: ${definition.worktree}`;
    const worktreeWidth = Math.max(
      0,
      innerWidth - Array.from(indicatorText).length - (indicatorText === '' ? 0 : 1),
    );
    putClippedText(surface, x + 1, y + 3, worktreeWidth, worktreeText, style);
    putClippedText(
      surface,
      x + 1 + Math.max(0, innerWidth - Array.from(indicatorText).length),
      y + 3,
      innerWidth,
      indicatorText,
      style,
    );
  }
  return surface;
}

// Re-exported for consumers which want to build node decorations without a React dependency.
export { drawHorizontal, drawVertical };
