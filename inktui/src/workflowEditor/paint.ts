import {
  type CellStyle,
  type CellSurface,
  drawHorizontal,
  drawVertical,
  putCell,
  putClippedText,
  putText,
} from '@murder/ui-core/render/cellSurface.js';
import type { GraphLayout } from '@murder/ui-core/workflowEditor/layout.js';
import type { EditorIssue, StageKey, Viewport } from '@murder/ui-core/workflowEditor/model.js';
import {
  type DirectionMask,
  E,
  glyphForMask,
  N,
  type Point,
  routeEdges,
  S,
  segmentMasks,
  W,
} from '@murder/ui-core/workflowEditor/routing.js';
import { stageStatusGlyph } from '@murder/ui-core/workflowEditor/statusDisplay.js';

export type ConnectLegality = 'add' | 'remove' | 'cycle' | 'invalid';

/** Light frame for an unselected node; heavy frame for the selected/candidate one (the same
 * weight-follows-focus rule the Pane chrome uses, so selection survives a colorless terminal). */
const NODE_FRAME = {
  light: { tl: '┌', tr: '┐', bl: '└', br: '┘', h: '─', v: '│' },
  heavy: { tl: '┏', tr: '┓', bl: '┗', br: '┛', h: '━', v: '┃' },
} as const;

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
  if (styles.connect !== undefined) {
    const preview = dependencyPreviewPoints(
      layout,
      styles.connect.candidate,
      styles.connect.target,
    );
    const previewStyle =
      styles.connect.legality === 'add'
        ? styles.candidateAdd
        : styles.connect.legality === 'remove'
          ? styles.candidateRemove
          : styles.candidateIllegal;
    for (const [position, mask] of segmentMasks(preview)) {
      const [x, y] = position.split(',').map(Number) as [number, number];
      const [sx, sy] = translate(x, y);
      const horizontal = (mask & (E | W)) !== 0;
      const vertical = (mask & (N | S)) !== 0;
      const glyph =
        horizontal && vertical ? glyphForMask(mask) : horizontal ? '┄' : vertical ? '┆' : ' ';
      putCell(surface, sx, sy, glyph, previewStyle);
    }
    const arrow = preview.at(-1);
    if (arrow !== undefined) {
      const [x, y] = translate(arrow.x, arrow.y);
      putCell(surface, x, y, '▷', previewStyle);
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
    const emphasized = candidateStyle !== undefined || stage.key === selected;
    const style: CellStyle =
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
              : (runtimeStyle ?? styles.node)) ??
      {};
    const definition = layout.graph.workflow.stages[stage.index];
    if (definition === undefined) continue;
    // A recessed variant for the supporting rows, so a node reads title-first instead of as one
    // uniform block of text. The selected node keeps every row at full strength.
    const support: CellStyle = emphasized ? { ...style, bold: false } : { ...style, dim: true };
    const rect = { x, y, width: positioned.rect.width, height: positioned.rect.height };
    const badge =
      issues.length > 0
        ? `!${issues.length}`
        : runtimeStatus === undefined
          ? ''
          : stageStatusGlyph(runtimeStatus);
    const badgeStyle =
      issues.length > 0 ? (styles.invalid ?? style) : emphasized ? style : (runtimeStyle ?? style);
    drawNode(surface, rect, style, emphasized, {
      index: stage.index + 1,
      title: definition.title || '(untitled)',
      badge,
      badgeStyle,
      supportStyle: support,
      tags: [
        definition.dependsOn.length === 0 ? 'root' : '',
        (layout.graph.outgoing.get(stage.key) ?? []).length === 0 ? 'sink' : '',
      ].filter((tag) => tag !== ''),
      trailing: definition.gate === 'auto' ? '' : `gate ${definition.gate}`,
    });
    const innerWidth = rect.width - 4;
    // An empty prompt is a real (blocking) state, so say so rather than leaving the node hollow.
    const bodyRows =
      definition.instructions === ''
        ? ['no prompt yet']
        : wrapText(definition.instructions, innerWidth, rect.height - 3);
    bodyRows.forEach((row, index) => {
      putText(
        surface,
        x + 2,
        y + 1 + index,
        row,
        definition.instructions === '' ? (styles.invalid ?? support) : support,
      );
    });
    const runtimeLine = [definition.harness, definition.model, definition.worktree]
      .filter((part): part is string => part !== null && part !== '')
      .join(' · ');
    putClippedText(
      surface,
      x + 2,
      y + rect.height - 2,
      innerWidth,
      clip(runtimeLine === '' ? 'runtime not set' : runtimeLine, innerWidth),
      runtimeLine === '' ? (styles.invalid ?? support) : support,
    );
  }
  return surface;
}

/** Truncate to `width` cells, marking the cut with `…` so a clipped word never reads as content. */
export function clip(text: string, width: number): string {
  const chars = Array.from(text);
  if (width <= 0) return '';
  if (chars.length <= width) return text;
  return `${chars.slice(0, Math.max(0, width - 1)).join('')}…`;
}

/**
 * Greedy word wrap into at most `maxLines` rows. Text that does not fit ends in `…` on the last
 * kept row, so a truncated stage description never masquerades as a complete one.
 */
export function wrapText(text: string, width: number, maxLines: number): readonly string[] {
  if (text === '' || width <= 0 || maxLines <= 0) return [];
  const words = text.split(/\s+/).filter((part) => part !== '');
  const lines: string[] = [];
  let current = '';
  for (const word of words) {
    const candidate = current === '' ? word : `${current} ${word}`;
    if (Array.from(candidate).length <= width) {
      current = candidate;
      continue;
    }
    if (lines.length + 1 === maxLines) {
      // Last available row: keep what fits of the overflowing line and mark the cut.
      return [...lines, clip(candidate, width)];
    }
    if (current !== '') lines.push(current);
    current = Array.from(word).length > width ? clip(word, width) : word;
  }
  if (current !== '') lines.push(current);
  return lines.slice(0, maxLines);
}

interface NodeChrome {
  readonly index: number;
  readonly title: string;
  readonly badge: string;
  readonly badgeStyle: CellStyle;
  readonly supportStyle: CellStyle;
  readonly tags: readonly string[];
  readonly trailing: string;
}

/**
 * Draw one node's frame with its title on the top rail and its structural tags on the bottom rail —
 * the same "labels live on the border" language as the {@link ../components/Pane.tsx Pane} chrome,
 * which keeps the node's interior for the stage's own words.
 */
function drawNode(
  surface: CellSurface,
  rect: { x: number; y: number; width: number; height: number },
  style: CellStyle,
  emphasized: boolean,
  chrome: NodeChrome,
): void {
  const frame = emphasized ? NODE_FRAME.heavy : NODE_FRAME.light;
  const right = rect.x + rect.width - 1;
  const bottom = rect.y + rect.height - 1;
  for (let x = rect.x + 1; x < right; x += 1) {
    putCell(surface, x, rect.y, frame.h, style);
    putCell(surface, x, bottom, frame.h, style);
  }
  for (let y = rect.y + 1; y < bottom; y += 1) {
    putCell(surface, rect.x, y, frame.v, style);
    putCell(surface, right, y, frame.v, style);
  }
  putCell(surface, rect.x, rect.y, frame.tl, style);
  putCell(surface, right, rect.y, frame.tr, style);
  putCell(surface, rect.x, bottom, frame.bl, style);
  putCell(surface, right, bottom, frame.br, style);

  // Top rail: `┌ 2 Run tests ─────── ✓ ┐` — index, title, then a right-anchored status/issue badge.
  // The badge keeps a space on each side so it never fuses with the `─` run or the corner glyph.
  const rail = rect.width - 2;
  const badge = chrome.badge === '' ? '' : ` ${chrome.badge} `;
  const prefix = `${chrome.index} `;
  const titleWidth = rail - 2 - Array.from(prefix).length - Array.from(badge).length;
  putText(surface, rect.x + 1, rect.y, ' ', style);
  putText(surface, rect.x + 2, rect.y, prefix, chrome.supportStyle);
  const title = clip(chrome.title, Math.max(0, titleWidth));
  putText(surface, rect.x + 2 + Array.from(prefix).length, rect.y, title, style);
  if (title.length > 0) {
    putText(
      surface,
      rect.x + 2 + Array.from(prefix).length + Array.from(title).length,
      rect.y,
      ' ',
      style,
    );
  }
  if (badge !== '') {
    putText(surface, right - Array.from(badge).length, rect.y, badge, chrome.badgeStyle);
  }

  // Bottom rail: structural tags left, a non-default gate right.
  const tags = chrome.tags.join(' ');
  if (tags !== '') {
    putText(surface, rect.x + 1, bottom, ` ${clip(tags, rail - 2)} `, chrome.supportStyle);
  }
  if (chrome.trailing !== '') {
    const trailing = ` ${clip(chrome.trailing, rail - 4)} `;
    putText(surface, right - Array.from(trailing).length, bottom, trailing, chrome.supportStyle);
  }
}

function dependencyPreviewPoints(
  layout: GraphLayout,
  sourceKey: StageKey,
  targetKey: StageKey,
): readonly Point[] {
  const source = layout.nodes.get(sourceKey);
  const target = layout.nodes.get(targetKey);
  if (source === undefined || target === undefined) return [];
  const from = {
    x: source.rect.x + source.rect.width,
    y: source.rect.y + Math.floor(source.rect.height / 2),
  };
  const to = {
    x: target.rect.x - 1,
    y: target.rect.y + Math.floor(target.rect.height / 2),
  };
  if (sourceKey === targetKey) {
    const right = source.rect.x + source.rect.width + 2;
    const bottom = source.rect.y + source.rect.height + 1;
    const left = source.rect.x - 2;
    return [
      from,
      { x: right, y: from.y },
      { x: right, y: bottom },
      { x: left, y: bottom },
      { x: left, y: to.y },
      to,
    ];
  }
  if (from.x < to.x) {
    const gutter = Math.floor((from.x + to.x) / 2);
    return [from, { x: gutter, y: from.y }, { x: gutter, y: to.y }, to];
  }
  const right = Math.max(from.x, target.rect.x + target.rect.width) + 2;
  const below = Math.max(source.rect.y + source.rect.height, target.rect.y + target.rect.height);
  return [from, { x: right, y: from.y }, { x: right, y: below }, { x: to.x, y: below }, to];
}

// Re-exported for consumers which want to build node decorations without a React dependency.
export { drawHorizontal, drawVertical };
