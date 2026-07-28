import type { EditorGraph, StrongComponent } from './graph.js';
import { buildEditorGraph } from './graph.js';
import type { EditorWorkflow, Rect, StageKey } from './model.js';

export const NODE_WIDTH = 24;
export const NODE_HEIGHT = 5;
export const RANK_GAP = 10;
export const ROW_GAP = 2;
// Five cells are reserved for a dangling dependency's leftward `? ───▶` repair stub.
export const CANVAS_MARGIN_X = 6;
export const CANVAS_MARGIN_Y = 1;

export interface LayoutNode {
  readonly key: StageKey;
  readonly rank: number;
  readonly order: number;
  readonly rect: Rect;
}
export interface GraphLayout {
  readonly nodes: ReadonlyMap<StageKey, LayoutNode>;
  readonly ranks: readonly (readonly StageKey[])[];
  readonly bounds: Rect;
  readonly graph: EditorGraph;
}

/** Stable SCC-DAG ranks. The deliberately fixed metrics make rendering testable and predictable. */
export function layoutWorkflow(workflow: EditorWorkflow): GraphLayout {
  const graph = buildEditorGraph(workflow);
  const byComponent = new Map(
    graph.components.map((component) => [component.key, component] as const),
  );
  const componentOf = graph.componentByNode;
  const incoming = new Map<string, Set<string>>();
  const outgoing = new Map<string, Set<string>>();
  for (const component of graph.components) {
    incoming.set(component.key, new Set());
    outgoing.set(component.key, new Set());
  }
  for (const edge of graph.resolvedEdges) {
    const source = componentOf.get(edge.source)?.key;
    const target = componentOf.get(edge.target)?.key;
    if (source === undefined || target === undefined) continue;
    if (source !== target) {
      (outgoing.get(source) as Set<string>).add(target);
      (incoming.get(target) as Set<string>).add(source);
    }
  }
  const rank = new Map<string, number>();
  const queue = [...graph.components]
    .filter((component) => (incoming.get(component.key) as Set<string>).size === 0)
    .sort((a, b) => componentOrder(graph, a, b));
  for (const component of queue) {
    rank.set(component.key, 0);
  }
  for (let cursor = 0; cursor < queue.length; cursor += 1) {
    const component = queue[cursor] as StrongComponent;
    for (const next of outgoing.get(component.key) as Set<string>) {
      rank.set(next, Math.max(rank.get(next) ?? 0, (rank.get(component.key) ?? 0) + 1));
      const parents = incoming.get(next) as Set<string>;
      parents.delete(component.key);
      if (parents.size === 0) queue.push(byComponent.get(next) as StrongComponent);
    }
  }
  const ranks: StageKey[][] = [];
  for (const component of graph.components) {
    const componentRank = rank.get(component.key) ?? 0;
    const row = ranks[componentRank] ?? [];
    row.push(...component.members);
    ranks[componentRank] = row;
  }
  // Definition order is both the initial crossing-minimizing order and a stable tie breaker.
  for (const row of ranks) {
    row.sort((a, b) => nodeOrder(graph, a, b));
  }
  for (let sweep = 0; sweep < 2; sweep += 1) {
    barycentric(ranks, graph, true);
    barycentric(ranks, graph, false);
  }
  const nodes = new Map<StageKey, LayoutNode>();
  let maxY = CANVAS_MARGIN_Y;
  ranks.forEach((row, rankIndex) => {
    row.forEach((key, order) => {
      const rect = {
        x: CANVAS_MARGIN_X + rankIndex * (NODE_WIDTH + RANK_GAP),
        y: CANVAS_MARGIN_Y + order * (NODE_HEIGHT + ROW_GAP),
        width: NODE_WIDTH,
        height: NODE_HEIGHT,
      };
      nodes.set(key, { key, rank: rankIndex, order, rect });
      maxY = Math.max(maxY, rect.y + rect.height);
    });
  });
  const maxRank = Math.max(0, ranks.length - 1);
  return {
    nodes,
    ranks,
    bounds: {
      x: 0,
      y: 0,
      width: CANVAS_MARGIN_X * 2 + (maxRank + 1) * NODE_WIDTH + maxRank * RANK_GAP,
      height: maxY + CANVAS_MARGIN_Y,
    },
    graph,
  };
}

function componentOrder(
  graph: EditorGraph,
  a: { readonly members: readonly StageKey[] },
  b: { readonly members: readonly StageKey[] },
): number {
  const aIndex = Math.min(
    ...a.members.map((key) => graph.nodes.get(key)?.index ?? Number.MAX_VALUE),
  );
  const bIndex = Math.min(
    ...b.members.map((key) => graph.nodes.get(key)?.index ?? Number.MAX_VALUE),
  );
  return aIndex - bIndex || (a.members[0] ?? '').localeCompare(b.members[0] ?? '');
}
function nodeOrder(graph: EditorGraph, a: StageKey, b: StageKey): number {
  const ai = graph.nodes.get(a)?.index ?? 0;
  const bi = graph.nodes.get(b)?.index ?? 0;
  return ai - bi || a.localeCompare(b);
}
function barycentric(ranks: StageKey[][], graph: EditorGraph, forward: boolean): void {
  const positions = new Map<StageKey, number>();
  for (const row of ranks) {
    row.forEach((key, index) => {
      positions.set(key, index);
    });
  }
  const range = forward ? [...ranks.keys()].slice(1) : [...ranks.keys()].slice(0, -1).reverse();
  for (const rankIndex of range) {
    const row = ranks[rankIndex] as StageKey[];
    row.sort((a, b) => {
      const edgesA = forward ? (graph.incoming.get(a) ?? []) : (graph.outgoing.get(a) ?? []);
      const edgesB = forward ? (graph.incoming.get(b) ?? []) : (graph.outgoing.get(b) ?? []);
      const average = (
        edges: readonly { source: StageKey; target: StageKey }[],
        key: StageKey,
      ): number =>
        edges.length === 0
          ? (positions.get(key) ?? 0)
          : edges.reduce(
              (sum, edge) => sum + (positions.get(forward ? edge.source : edge.target) ?? 0),
              0,
            ) / edges.length;
      return average(edgesA, a) - average(edgesB, b) || nodeOrder(graph, a, b);
    });
  }
}
