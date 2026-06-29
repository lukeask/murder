import type { RecipientTargetState } from '../selectors/conversationsSelectors.js';
import type { Rect } from '../terminal/geometry.js';
import {
  CHAT_FOCUS,
  type FocusGraphTargetId,
  type FocusId,
  type FocusTarget,
  focusTargetFromFocusId,
  recipientTargetIdFromVertexId,
  recipientTargetVertexId,
} from './focusIds.js';
import { type Direction, directionalFocusTarget, type FocusCandidate } from './geometry.js';

export interface FocusVertex {
  readonly id: FocusGraphTargetId;
  readonly focusId: FocusId;
  readonly kind: 'pane' | 'recipientTarget';
  readonly rect: Rect;
  readonly orderKey: number;
  readonly recipientTargetId?: string | null;
}

export interface FocusEdge {
  readonly from: FocusGraphTargetId;
  readonly to: FocusGraphTargetId;
  readonly direction: Direction;
  readonly traversal: 'ordinaryPaneAdjacency' | 'syntheticRecipientTarget';
}

export interface FocusGraph {
  readonly vertices: ReadonlyMap<FocusGraphTargetId, FocusVertex>;
  readonly edges: readonly FocusEdge[];
  readonly paneVertexIds: readonly FocusGraphTargetId[];
  readonly recipientTargetVertexIds: readonly FocusGraphTargetId[];
  readonly activeRecipientTargetVertexId: FocusGraphTargetId | null;
}

export interface FocusGraphState {
  readonly activeTargetId: string | null;
  readonly previouslyInhabitedVertexId: FocusGraphTargetId | null;
  readonly openPaneIdsByOpenedAt: readonly FocusGraphTargetId[];
  readonly previousLockedVisibleTargetIds: readonly string[];
  readonly previousFavoriteOnlyTargetIds: readonly string[];
}

export interface FocusPaneGeometry {
  readonly id: FocusId;
  readonly rect: Rect;
  readonly orderKey?: number;
}

export interface FocusGraphRecipientTarget {
  readonly targetId: string;
  readonly orderKey?: number;
  readonly active?: boolean;
}

export interface BuildFocusGraphInput {
  readonly panes: readonly FocusPaneGeometry[];
  readonly chatRect?: Rect | null;
  readonly recipientTargets?: readonly FocusGraphRecipientTarget[];
  readonly state?: FocusGraphState;
}

export interface ResolvedFocus {
  readonly id: FocusId;
  readonly target: FocusTarget;
}

export interface FocusNavigationResult {
  readonly targetId: FocusGraphTargetId | null;
  readonly focusId: FocusId | null;
  readonly target: FocusTarget | null;
  readonly recipientTargetId: string | null;
  readonly edge: FocusEdge | null;
  readonly state: FocusGraphState;
}

const DEFAULT_RECIPIENT_TARGET = '__active__';

export const EMPTY_FOCUS_GRAPH_STATE: FocusGraphState = {
  activeTargetId: null,
  previouslyInhabitedVertexId: null,
  openPaneIdsByOpenedAt: [],
  previousLockedVisibleTargetIds: [],
  previousFavoriteOnlyTargetIds: [],
};

function rectHasArea(rect: Rect): boolean {
  return rect.width > 0 && rect.height > 0;
}

function geometryOrderKey(rect: Rect): number {
  return rect.y * 100_000 + rect.x;
}

function pushPaneVertex(
  vertices: Map<FocusGraphTargetId, FocusVertex>,
  paneVertexIds: FocusGraphTargetId[],
  id: FocusId,
  rect: Rect,
  orderKey?: number,
): void {
  if (id === CHAT_FOCUS || !rectHasArea(rect)) {
    return;
  }
  const vertex: FocusVertex = {
    id,
    focusId: id,
    kind: 'pane',
    rect,
    orderKey: orderKey ?? geometryOrderKey(rect),
  };
  vertices.set(id, vertex);
  paneVertexIds.push(id);
}

function recipientTargetIdForVirtualVertex(vertexId: FocusGraphTargetId): string | null {
  return recipientTargetIdFromVertexId(vertexId);
}

function focusIdForVertex(vertex: FocusVertex): FocusId {
  return vertex.kind === 'recipientTarget' ? CHAT_FOCUS : vertex.focusId;
}

function sortedVertexIds(
  vertices: ReadonlyMap<FocusGraphTargetId, FocusVertex>,
  ids: readonly FocusGraphTargetId[],
): readonly FocusGraphTargetId[] {
  return [...ids].sort((a, b) => {
    const av = vertices.get(a);
    const bv = vertices.get(b);
    if (av === undefined || bv === undefined) {
      return 0;
    }
    if (av.orderKey !== bv.orderKey) {
      return av.orderKey - bv.orderKey;
    }
    if (av.rect.y !== bv.rect.y) {
      return av.rect.y - bv.rect.y;
    }
    if (av.rect.x !== bv.rect.x) {
      return av.rect.x - bv.rect.x;
    }
    return String(a).localeCompare(String(b));
  });
}

function uniqueStrings(values: readonly (string | null | undefined)[]): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  for (const value of values) {
    if (value === null || value === undefined || seen.has(value)) {
      continue;
    }
    seen.add(value);
    result.push(value);
  }
  return result;
}

export function focusPaneGeometriesFromRects(
  rects: ReadonlyMap<FocusId, Rect>,
): readonly FocusPaneGeometry[] {
  return [...rects]
    .filter(([id, rect]) => id !== CHAT_FOCUS && rectHasArea(rect))
    .map(([id, rect]) => ({ id, rect, orderKey: geometryOrderKey(rect) }));
}

export function normalizeFocusGraphRecipientTargets(
  state: RecipientTargetState,
): readonly FocusGraphRecipientTarget[] {
  const targetIds = uniqueStrings([
    ...state.lockedVisibleTargetIds,
    state.ephemeralTargetId,
    ...state.favoriteOnlyTargetIds,
    state.activeTargetId,
  ]);
  return targetIds.map((targetId, index) => ({
    targetId,
    orderKey: index,
    active: targetId === state.activeTargetId,
  }));
}

function resolveActiveRecipientTarget(
  recipientTargets: readonly FocusGraphRecipientTarget[],
): string {
  const marked = recipientTargets.find((target) => target.active === true);
  return marked?.targetId ?? recipientTargets[0]?.targetId ?? DEFAULT_RECIPIENT_TARGET;
}

function buildOrdinaryEdge(
  vertices: ReadonlyMap<FocusGraphTargetId, FocusVertex>,
  from: FocusGraphTargetId,
  direction: Direction,
  activeRecipientTargetVertexId: FocusGraphTargetId | null,
  paneVertexIds: readonly FocusGraphTargetId[],
  state: FocusGraphState | undefined,
): FocusEdge | null {
  const source = vertices.get(from);
  if (source === undefined) {
    return null;
  }
  const orderedPaneVertexIds = orderPaneCandidatesForDirection(
    vertices,
    paneVertexIds,
    direction,
    state,
  );
  const candidateIds =
    source.kind === 'recipientTarget'
      ? [from, ...orderedPaneVertexIds]
      : activeRecipientTargetVertexId === null
        ? orderedPaneVertexIds
        : [...orderedPaneVertexIds, activeRecipientTargetVertexId];
  const candidates: FocusCandidate<FocusGraphTargetId>[] = [];
  for (const id of candidateIds) {
    const vertex = vertices.get(id);
    if (vertex !== undefined) {
      candidates.push({ id, rect: vertex.rect });
    }
  }
  const target = directionalFocusTarget(direction, from, candidates);
  return target === null
    ? null
    : { from, to: target, direction, traversal: 'ordinaryPaneAdjacency' };
}

function orderPaneCandidatesForDirection(
  vertices: ReadonlyMap<FocusGraphTargetId, FocusVertex>,
  paneVertexIds: readonly FocusGraphTargetId[],
  direction: Direction,
  state: FocusGraphState | undefined,
): readonly FocusGraphTargetId[] {
  if (state === undefined) {
    return paneVertexIds;
  }
  const remaining = new Set(paneVertexIds);
  const ordered: FocusGraphTargetId[] = [];
  const pushKnown = (ids: readonly FocusGraphTargetId[]) => {
    for (const id of ids) {
      if (!remaining.delete(id)) {
        continue;
      }
      ordered.push(id);
    }
  };
  if (state.previouslyInhabitedVertexId !== null) {
    pushKnown([state.previouslyInhabitedVertexId]);
  }
  pushKnown([...state.openPaneIdsByOpenedAt].reverse());
  pushKnown(projectPaneVertexIds(vertices, paneVertexIds, direction));
  pushKnown(paneVertexIds);
  return ordered;
}

function projectPaneVertexIds(
  vertices: ReadonlyMap<FocusGraphTargetId, FocusVertex>,
  paneVertexIds: readonly FocusGraphTargetId[],
  direction: Direction,
): readonly FocusGraphTargetId[] {
  const horizontal = direction === 'left' || direction === 'right';
  return [...paneVertexIds].sort((a, b) => {
    const av = vertices.get(a);
    const bv = vertices.get(b);
    if (av === undefined || bv === undefined) return 0;
    return horizontal
      ? av.rect.y - bv.rect.y || av.rect.x - bv.rect.x || av.orderKey - bv.orderKey
      : av.rect.x - bv.rect.x || av.rect.y - bv.rect.y || av.orderKey - bv.orderKey;
  });
}

export function buildFocusGraph(input: BuildFocusGraphInput): FocusGraph {
  const vertices = new Map<FocusGraphTargetId, FocusVertex>();
  const paneVertexIds: FocusGraphTargetId[] = [];
  const chatRect = input.chatRect != null && rectHasArea(input.chatRect) ? input.chatRect : null;

  for (const pane of input.panes) {
    pushPaneVertex(vertices, paneVertexIds, pane.id, pane.rect, pane.orderKey);
  }

  const recipientTargetVertexIds: FocusGraphTargetId[] = [];
  let activeRecipientTargetVertexId: FocusGraphTargetId | null = null;
  if (chatRect !== null) {
    const recipientTargets = input.recipientTargets ?? [];
    const sourceTargets =
      recipientTargets.length > 0
        ? recipientTargets
        : [{ targetId: DEFAULT_RECIPIENT_TARGET, active: true }];
    const activeTargetId = resolveActiveRecipientTarget(sourceTargets);
    sourceTargets.forEach((target, index) => {
      const id = recipientTargetVertexId(target.targetId);
      const vertex: FocusVertex = {
        id,
        focusId: CHAT_FOCUS,
        kind: 'recipientTarget',
        rect: chatRect,
        orderKey: target.orderKey ?? index,
        recipientTargetId: target.targetId,
      };
      vertices.set(id, vertex);
      recipientTargetVertexIds.push(id);
      if (target.targetId === activeTargetId) {
        activeRecipientTargetVertexId = id;
      }
    });
    if (activeRecipientTargetVertexId === null) {
      activeRecipientTargetVertexId = recipientTargetVertexIds[0] ?? null;
    }
  }

  const sortedPaneVertexIds = sortedVertexIds(vertices, paneVertexIds);
  const sortedRecipientTargetVertexIds = sortedVertexIds(vertices, recipientTargetVertexIds);
  const edges: FocusEdge[] = [];
  const edgeSources = [...sortedPaneVertexIds];
  if (activeRecipientTargetVertexId !== null) {
    edgeSources.push(activeRecipientTargetVertexId);
  }
  for (const from of edgeSources) {
    for (const direction of ['left', 'right', 'up', 'down'] as const) {
      const edge = buildOrdinaryEdge(
        vertices,
        from,
        direction,
        activeRecipientTargetVertexId,
        sortedPaneVertexIds,
        input.state,
      );
      if (edge !== null) {
        edges.push(edge);
      }
    }
  }

  sortedRecipientTargetVertexIds.forEach((from, index) => {
    const count = sortedRecipientTargetVertexIds.length;
    if (count < 2) {
      return;
    }
    const left = sortedRecipientTargetVertexIds[(index - 1 + count) % count];
    const right = sortedRecipientTargetVertexIds[(index + 1) % count];
    if (left !== undefined) {
      edges.push({ from, to: left, direction: 'left', traversal: 'syntheticRecipientTarget' });
    }
    if (right !== undefined) {
      edges.push({ from, to: right, direction: 'right', traversal: 'syntheticRecipientTarget' });
    }
  });

  return {
    vertices,
    edges,
    paneVertexIds: sortedPaneVertexIds,
    recipientTargetVertexIds: sortedRecipientTargetVertexIds,
    activeRecipientTargetVertexId,
  };
}

function graphTraversalState(
  graph: FocusGraph,
  activeTargetId: string | null,
  previouslyInhabitedVertexId: FocusGraphTargetId | null,
  lockedVisibleTargetIds: readonly string[],
  favoriteOnlyTargetIds: readonly string[],
  previousState: FocusGraphState,
): FocusGraphState {
  const livePaneIds = new Set(graph.paneVertexIds);
  const byOpenedAt = previousState.openPaneIdsByOpenedAt.filter((id) => livePaneIds.has(id));
  return {
    activeTargetId,
    previouslyInhabitedVertexId,
    openPaneIdsByOpenedAt: byOpenedAt,
    previousLockedVisibleTargetIds: [...lockedVisibleTargetIds],
    previousFavoriteOnlyTargetIds: [...favoriteOnlyTargetIds],
  };
}

export function resolveEffectiveFocus(intended: FocusId, graph: FocusGraph): FocusId {
  if (intended === CHAT_FOCUS) {
    return CHAT_FOCUS;
  }
  const vertex = graph.vertices.get(intended);
  return vertex?.kind === 'pane' ? intended : CHAT_FOCUS;
}

export function resolveEffectiveFocusTarget(intended: FocusId, graph: FocusGraph): ResolvedFocus {
  const id = resolveEffectiveFocus(intended, graph);
  const target = focusTargetFromFocusId(id);
  return { id, target: target ?? { kind: 'composer' } };
}

export function refreshFocusGraphState(
  graph: FocusGraph,
  state: FocusGraphState = EMPTY_FOCUS_GRAPH_STATE,
): FocusGraphState {
  const activeTargetId =
    graph.activeRecipientTargetVertexId === null
      ? state.activeTargetId
      : recipientTargetIdForVirtualVertex(graph.activeRecipientTargetVertexId);
  return graphTraversalState(
    graph,
    activeTargetId,
    state.previouslyInhabitedVertexId,
    state.previousLockedVisibleTargetIds,
    state.previousFavoriteOnlyTargetIds,
    state,
  );
}

function sourceVertexIdForFocus(current: FocusId, graph: FocusGraph): FocusGraphTargetId | null {
  if (current === CHAT_FOCUS) {
    return graph.activeRecipientTargetVertexId;
  }
  return graph.vertices.has(current) ? current : graph.activeRecipientTargetVertexId;
}

function edgeForDirection(
  graph: FocusGraph,
  source: FocusGraphTargetId,
  direction: Direction,
  state: FocusGraphState,
): FocusEdge | null {
  const sourceVertex = graph.vertices.get(source);
  const matches = graph.edges.filter(
    (candidate) => candidate.from === source && candidate.direction === direction,
  );
  if (sourceVertex?.kind === 'recipientTarget' && (direction === 'left' || direction === 'right')) {
    return (
      matches.find((candidate) => candidate.traversal === 'syntheticRecipientTarget') ??
      matches[0] ??
      null
    );
  }
  const ordinary = matches.filter((candidate) => candidate.traversal === 'ordinaryPaneAdjacency');
  const previous = ordinary.find((candidate) => candidate.to === state.previouslyInhabitedVertexId);
  return previous ?? ordinary[0] ?? null;
}

export function navigateFocus(
  graph: FocusGraph,
  current: FocusId,
  direction: Direction,
  state: FocusGraphState = EMPTY_FOCUS_GRAPH_STATE,
): FocusNavigationResult {
  const source = sourceVertexIdForFocus(current, graph);
  const edge = source === null ? null : edgeForDirection(graph, source, direction, state);
  const targetVertex = edge === null ? null : (graph.vertices.get(edge.to) ?? null);
  const targetId = targetVertex?.id ?? null;
  const recipientTargetId =
    targetVertex?.kind === 'recipientTarget'
      ? (targetVertex.recipientTargetId ?? recipientTargetIdForVirtualVertex(targetVertex.id))
      : null;
  const focusId = targetVertex === null ? null : focusIdForVertex(targetVertex);
  const activeTargetId =
    recipientTargetId ??
    (graph.activeRecipientTargetVertexId === null
      ? state.activeTargetId
      : recipientTargetIdForVirtualVertex(graph.activeRecipientTargetVertexId));
  const nextState = graphTraversalState(
    graph,
    activeTargetId,
    edge === null ? state.previouslyInhabitedVertexId : source,
    state.previousLockedVisibleTargetIds,
    state.previousFavoriteOnlyTargetIds,
    state,
  );
  return {
    targetId,
    focusId,
    target: focusId === null ? null : focusTargetFromFocusId(focusId),
    recipientTargetId,
    edge,
    state: nextState,
  };
}
