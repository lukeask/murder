import type { GraphLayout } from './layout.js';
import type { StageKey } from './model.js';

export type DirectionMask = number;
export const N = 1;
export const E = 2;
export const S = 4;
export const W = 8;
export interface Point {
  readonly x: number;
  readonly y: number;
}
export interface RoutedEdge {
  readonly source: StageKey;
  readonly target: StageKey;
  readonly points: readonly Point[];
}
export interface RoutedStub {
  readonly target: StageKey;
  readonly dependency: string;
  readonly points: readonly Point[];
}

/** Orthogonal monotone routes. Individual rank gutters keep long edges out of node columns. */
export function routeEdges(layout: GraphLayout): {
  readonly edges: readonly RoutedEdge[];
  readonly stubs: readonly RoutedStub[];
} {
  const resolved = [...layout.graph.resolvedEdges].sort((a, b) => compareRoutedEdges(layout, a, b));
  const lanes = assignBoundaryLanes(layout, resolved);
  const edges = resolved.map((edge): RoutedEdge => {
    const source = requiredLayoutNode(layout, edge.source);
    const target = requiredLayoutNode(layout, edge.target);
    const from = {
      x: source.rect.x + source.rect.width,
      y: source.rect.y + Math.floor(source.rect.height / 2),
    };
    const to = { x: target.rect.x - 1, y: target.rect.y + Math.floor(target.rect.height / 2) };
    if (source.rank >= target.rank) {
      const gutter = source.rect.x + source.rect.width + 2;
      return {
        source: edge.source,
        target: edge.target,
        points: [from, { x: gutter, y: from.y }, { x: gutter, y: to.y }, to],
      };
    }
    const points: Point[] = [from];
    const span = target.rank - source.rank;
    let currentY = from.y;
    for (let boundary = source.rank; boundary < target.rank; boundary += 1) {
      const lane = lanes.get(boundary)?.get(edgeKey(edge)) ?? 0;
      const gutter = requiredRankX(layout, boundary) + source.rect.width + 2 + lane;
      const step = boundary - source.rank + 1;
      const nextY = step === span ? to.y : Math.round(from.y + ((to.y - from.y) * step) / span);
      points.push({ x: gutter, y: currentY }, { x: gutter, y: nextY });
      currentY = nextY;
    }
    points.push(to);
    return { source: edge.source, target: edge.target, points };
  });
  const stubs = layout.graph.unresolvedEdges.map((edge): RoutedStub => {
    const target = requiredLayoutNode(layout, edge.target);
    const y = target.rect.y + Math.floor(target.rect.height / 2);
    return {
      target: edge.target,
      dependency: edge.dependency,
      points: [
        { x: target.rect.x - 1, y },
        { x: target.rect.x - 5, y },
      ],
    };
  });
  return { edges, stubs };
}

function requiredLayoutNode(layout: GraphLayout, key: StageKey) {
  const node = layout.nodes.get(key);
  if (node === undefined) throw new Error(`layout is missing workflow node ${key}`);
  return node;
}

function requiredRankX(layout: GraphLayout, rank: number): number {
  const key = layout.ranks[rank]?.[0];
  if (key === undefined) throw new Error(`layout is missing rank ${rank}`);
  return requiredLayoutNode(layout, key).rect.x;
}

function edgeKey(edge: { readonly source: StageKey; readonly target: StageKey }): string {
  return `${edge.source}\0${edge.target}`;
}

function compareRoutedEdges(
  layout: GraphLayout,
  a: { readonly source: StageKey; readonly target: StageKey },
  b: { readonly source: StageKey; readonly target: StageKey },
): number {
  const sourceA = requiredLayoutNode(layout, a.source);
  const sourceB = requiredLayoutNode(layout, b.source);
  const targetA = requiredLayoutNode(layout, a.target);
  const targetB = requiredLayoutNode(layout, b.target);
  return (
    sourceA.rect.y - sourceB.rect.y ||
    targetA.rect.y - targetB.rect.y ||
    (layout.graph.nodes.get(a.source)?.index ?? 0) -
      (layout.graph.nodes.get(b.source)?.index ?? 0) ||
    (layout.graph.nodes.get(a.target)?.index ?? 0) - (layout.graph.nodes.get(b.target)?.index ?? 0)
  );
}

function assignBoundaryLanes(
  layout: GraphLayout,
  edges: readonly { readonly source: StageKey; readonly target: StageKey }[],
): ReadonlyMap<number, ReadonlyMap<string, number>> {
  const result = new Map<number, Map<string, number>>();
  for (let boundary = 0; boundary < layout.ranks.length - 1; boundary += 1) {
    const crossing = edges.filter((edge) => {
      const source = requiredLayoutNode(layout, edge.source);
      const target = requiredLayoutNode(layout, edge.target);
      return source.rank <= boundary && target.rank > boundary;
    });
    const intervalsByLane: { from: number; to: number }[][] = [];
    const boundaryLanes = new Map<string, number>();
    for (const edge of crossing) {
      const source = requiredLayoutNode(layout, edge.source);
      const target = requiredLayoutNode(layout, edge.target);
      const sourceY = source.rect.y + Math.floor(source.rect.height / 2);
      const targetY = target.rect.y + Math.floor(target.rect.height / 2);
      const interval = { from: Math.min(sourceY, targetY), to: Math.max(sourceY, targetY) };
      let lane = intervalsByLane.findIndex((existing) =>
        existing.every((used) => interval.to < used.from || interval.from > used.to),
      );
      if (lane < 0) {
        lane = intervalsByLane.length;
        intervalsByLane.push([]);
      }
      intervalsByLane[lane]?.push(interval);
      boundaryLanes.set(edgeKey(edge), lane);
    }
    result.set(boundary, boundaryLanes);
  }
  return result;
}

export function segmentMasks(points: readonly Point[]): ReadonlyMap<string, DirectionMask> {
  const masks = new Map<string, DirectionMask>();
  const add = (point: Point, mask: DirectionMask): void => {
    masks.set(`${point.x},${point.y}`, (masks.get(`${point.x},${point.y}`) ?? 0) | mask);
  };
  for (let index = 1; index < points.length; index += 1) {
    const a = points[index - 1];
    const b = points[index];
    if (a === undefined || b === undefined) continue;
    const dx = Math.sign(b.x - a.x);
    const dy = Math.sign(b.y - a.y);
    let point = a;
    while (point.x !== b.x || point.y !== b.y) {
      const next = { x: point.x + dx, y: point.y + dy };
      add(point, dx > 0 ? E : dx < 0 ? W : dy > 0 ? S : N);
      add(next, dx > 0 ? W : dx < 0 ? E : dy > 0 ? N : S);
      point = next;
    }
  }
  return masks;
}

export function glyphForMask(mask: DirectionMask, ascii = false): string {
  if (ascii)
    return (mask & (N | S)) !== 0 && (mask & (E | W)) !== 0
      ? '+'
      : (mask & (N | S)) !== 0
        ? '|'
        : '-';
  const table: Record<number, string> = {
    [N | S]: '│',
    [E | W]: '─',
    [E | S]: '┌',
    [W | S]: '┐',
    [E | N]: '└',
    [W | N]: '┘',
    [N | E | S]: '├',
    [N | W | S]: '┤',
    [E | S | W]: '┬',
    [N | E | W]: '┴',
    [N | E | S | W]: '┼',
  };
  return table[mask] ?? (mask & (N | S) ? '│' : '─');
}
