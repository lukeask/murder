import type { RecipientTargetState } from '../layout/paneLayoutTypes.js';
import {
  CHAT_FOCUS,
  focusTargetFromFocusId,
  type FocusGraphTargetId,
  type FocusId,
  type FocusTarget,
  recipientTargetIdFromVertexId,
  recipientTargetVertexId,
} from './focusIds.js';
import {
  type Direction,
  directionalFocusTarget,
  type FocusCandidate,
  type Rect,
} from './geometry.js';

export type PaneRect = Rect;

export interface FocusVertex {
  readonly id: FocusGraphTargetId;
  readonly focusId: FocusId;
  readonly kind: 'pane' | 'recipientTarget';
  readonly rect: PaneRect;
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
  readonly openPaneIdsByX: readonly FocusGraphTargetId[];
  readonly openPaneIdsByY: readonly FocusGraphTargetId[];
  readonly previousLockedVisibleTargetIds: readonly string[];
  readonly previousFavoriteOnlyTargetIds: readonly string[];
}

export interface FocusGraphAllocation {
  readonly id: FocusId;
  readonly rect: PaneRect;
  readonly orderKey?: number;
  readonly mounted?: boolean;
  readonly painted?: boolean;
  readonly hidden?: boolean;
  readonly denied?: boolean;
}

export interface FocusGraphRecipientTarget {
  readonly targetId: string;
  readonly orderKey?: number;
  readonly active?: boolean;
}

export interface BuildFocusGraphInput {
  readonly rects?: ReadonlyMap<FocusId, PaneRect>;
  readonly allocations?: readonly FocusGraphAllocation[];
  readonly recipientTargets?: RecipientTargetState | readonly FocusGraphRecipientTarget[];
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
  openPaneIdsByX: [],
  openPaneIdsByY: [],
  previousLockedVisibleTargetIds: [],
  previousFavoriteOnlyTargetIds: [],
};

function rectHasArea(rect: PaneRect): boolean {
  return rect.width > 0 && rect.height > 0;
}

function geometryOrderKey(rect: PaneRect): number {
  return rect.y * 100_000 + rect.x;
}

function isLiveAllocation(allocation: FocusGraphAllocation): boolean {
  if (allocation.denied === true || allocation.hidden === true) return false;
  if (allocation.mounted === false || allocation.painted === false) return false;
  return rectHasArea(allocation.rect);
}

function pushPaneVertex(
  vertices: Map<FocusGraphTargetId, FocusVertex>,
  paneVertexIds: FocusGraphTargetId[],
  id: FocusId,
  rect: PaneRect,
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

function isPartitionedRecipientTargetState(
  targets: RecipientTargetState | readonly FocusGraphRecipientTarget[] | undefined,
): targets is RecipientTargetState {
  return (
    targets !== undefined &&
    !Array.isArray(targets) &&
    'activeTargetId' in targets &&
    'lockedVisibleTargetIds' in targets
  );
}

function recipientTargetsFromInput(input: BuildFocusGraphInput): {
  readonly targets: readonly FocusGraphRecipientTarget[];
  readonly activeTargetId: string | null | undefined;
  readonly lockedVisibleTargetIds: readonly string[];
  readonly favoriteOnlyTargetIds: readonly string[];
} {
  const rawTargets = input.recipientTargets;
  if (isPartitionedRecipientTargetState(rawTargets)) {
    const state = rawTargets;
    const targetIds = uniqueStrings([
      ...state.lockedVisibleTargetIds,
      state.ephemeralTargetId,
      ...state.favoriteOnlyTargetIds,
      state.activeTargetId,
    ]);
    const targets = targetIds.map((targetId, index) => ({
      targetId,
      orderKey: index,
      active: targetId === state.activeTargetId,
    }));
    return {
      targets,
      activeTargetId: state.activeTargetId,
      lockedVisibleTargetIds: state.lockedVisibleTargetIds,
      favoriteOnlyTargetIds: state.favoriteOnlyTargetIds,
    };
  }
  const targets = (rawTargets ?? []) as readonly FocusGraphRecipientTarget[];
  return {
    targets,
    activeTargetId: undefined,
    lockedVisibleTargetIds: [],
    favoriteOnlyTargetIds: [],
  };
}

function resolveActiveRecipientTarget(
  recipientTargets: readonly FocusGraphRecipientTarget[],
  explicitActive: string | null | undefined,
): string {
  if (explicitActive !== undefined && explicitActive !== null) {
    return explicitActive;
  }
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
  const orderedPaneVertexIds = orderPaneCandidatesForDirection(paneVertexIds, direction, state);
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
  pushKnown(
    direction === 'left' || direction === 'right' ? state.openPaneIdsByY : state.openPaneIdsByX,
  );
  pushKnown(state.openPaneIdsByOpenedAt);
  pushKnown(paneVertexIds);
  return ordered;
}

export function buildFocusGraph(input: BuildFocusGraphInput): FocusGraph {
  const vertices = new Map<FocusGraphTargetId, FocusVertex>();
  const paneVertexIds: FocusGraphTargetId[] = [];
  let chatRect: PaneRect | null = null;

  if (input.rects !== undefined) {
    for (const [id, rect] of input.rects) {
      if (id === CHAT_FOCUS) {
        if (rectHasArea(rect)) {
          chatRect = rect;
        }
      } else {
        pushPaneVertex(vertices, paneVertexIds, id, rect);
      }
    }
  }

  if (input.allocations !== undefined) {
    for (const allocation of input.allocations) {
      if (!isLiveAllocation(allocation)) {
        continue;
      }
      if (allocation.id === CHAT_FOCUS) {
        chatRect = allocation.rect;
      } else {
        pushPaneVertex(
          vertices,
          paneVertexIds,
          allocation.id,
          allocation.rect,
          allocation.orderKey,
        );
      }
    }
  }

  const recipientTargetVertexIds: FocusGraphTargetId[] = [];
  let activeRecipientTargetVertexId: FocusGraphTargetId | null = null;
  const recipientTargetInput = recipientTargetsFromInput(input);
  if (chatRect !== null) {
    const sourceTargets =
      recipientTargetInput.targets.length > 0
        ? recipientTargetInput.targets
        : [{ targetId: DEFAULT_RECIPIENT_TARGET, active: true }];
    const activeTargetId = resolveActiveRecipientTarget(
      sourceTargets,
      recipientTargetInput.activeTargetId,
    );
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
  const byX = [...graph.paneVertexIds].sort((a, b) => {
    const av = graph.vertices.get(a);
    const bv = graph.vertices.get(b);
    if (av === undefined || bv === undefined) return 0;
    return av.rect.x - bv.rect.x || av.rect.y - bv.rect.y || av.orderKey - bv.orderKey;
  });
  const byY = [...graph.paneVertexIds].sort((a, b) => {
    const av = graph.vertices.get(a);
    const bv = graph.vertices.get(b);
    if (av === undefined || bv === undefined) return 0;
    return av.rect.y - bv.rect.y || av.rect.x - bv.rect.x || av.orderKey - bv.orderKey;
  });
  return {
    activeTargetId,
    previouslyInhabitedVertexId,
    openPaneIdsByOpenedAt: byOpenedAt,
    openPaneIdsByX: byX,
    openPaneIdsByY: byY,
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

export function resolveEffectiveFocusTarget(
  intended: FocusId,
  graph: FocusGraph,
): ResolvedFocus {
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
    targetId ?? source ?? state.previouslyInhabitedVertexId,
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
