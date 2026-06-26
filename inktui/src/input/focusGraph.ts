import {
  chatTargetVertexId,
  CHAT_FOCUS,
  type FocusGraphTargetId,
  type FocusId,
  isChatTargetVertexId,
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
  readonly kind: 'pane' | 'chatTarget';
  readonly rect: PaneRect;
  readonly orderKey: number;
  readonly chatTargetId?: string | null;
}

export interface FocusEdge {
  readonly from: FocusGraphTargetId;
  readonly to: FocusGraphTargetId;
  readonly direction: Direction;
  readonly traversal: 'ordinaryPaneAdjacency' | 'syntheticChatTarget';
}

export interface FocusGraph {
  readonly vertices: ReadonlyMap<FocusGraphTargetId, FocusVertex>;
  readonly edges: readonly FocusEdge[];
  readonly paneVertexIds: readonly FocusGraphTargetId[];
  readonly chatTargetVertexIds: readonly FocusGraphTargetId[];
  readonly activeChatTargetVertexId: FocusGraphTargetId | null;
}

export interface FocusGraphState {
  readonly activeChatTargetId: string | null;
  readonly lastTargetByDirection: Readonly<Partial<Record<Direction, FocusGraphTargetId>>>;
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

export interface FocusGraphChatTarget {
  readonly targetId: string;
  readonly orderKey?: number;
  readonly active?: boolean;
}

export interface BuildFocusGraphInput {
  readonly rects?: ReadonlyMap<FocusId, PaneRect>;
  readonly allocations?: readonly FocusGraphAllocation[];
  readonly chatTargets?: readonly FocusGraphChatTarget[];
  readonly activeChatTargetId?: string | null;
}

export interface FocusNavigationResult {
  readonly targetId: FocusGraphTargetId | null;
  readonly focusId: FocusId | null;
  readonly chatTargetId: string | null;
  readonly edge: FocusEdge | null;
  readonly state: FocusGraphState;
}

const DEFAULT_CHAT_TARGET = '__active__';

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

function targetIdForVirtualVertex(vertexId: FocusGraphTargetId): string | null {
  if (!isChatTargetVertexId(vertexId)) {
    return null;
  }
  return vertexId.slice('chat:target:'.length);
}

function focusIdForVertex(vertex: FocusVertex): FocusId {
  return vertex.kind === 'chatTarget' ? CHAT_FOCUS : vertex.focusId;
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

function resolveActiveChatTarget(
  chatTargets: readonly FocusGraphChatTarget[],
  explicitActive: string | null | undefined,
): string {
  if (explicitActive !== undefined && explicitActive !== null) {
    return explicitActive;
  }
  const marked = chatTargets.find((target) => target.active === true);
  return marked?.targetId ?? chatTargets[0]?.targetId ?? DEFAULT_CHAT_TARGET;
}

function buildOrdinaryEdge(
  vertices: ReadonlyMap<FocusGraphTargetId, FocusVertex>,
  from: FocusGraphTargetId,
  direction: Direction,
  activeChatTargetVertexId: FocusGraphTargetId | null,
  paneVertexIds: readonly FocusGraphTargetId[],
): FocusEdge | null {
  const source = vertices.get(from);
  if (source === undefined) {
    return null;
  }
  const candidateIds =
    source.kind === 'chatTarget'
      ? [from, ...paneVertexIds]
      : activeChatTargetVertexId === null
        ? paneVertexIds
        : [...paneVertexIds, activeChatTargetVertexId];
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
        pushPaneVertex(vertices, paneVertexIds, allocation.id, allocation.rect, allocation.orderKey);
      }
    }
  }

  const chatTargetVertexIds: FocusGraphTargetId[] = [];
  let activeChatTargetVertexId: FocusGraphTargetId | null = null;
  if (chatRect !== null) {
    const sourceTargets =
      input.chatTargets !== undefined && input.chatTargets.length > 0
        ? input.chatTargets
        : [{ targetId: DEFAULT_CHAT_TARGET, active: true }];
    const activeTargetId = resolveActiveChatTarget(sourceTargets, input.activeChatTargetId);
    sourceTargets.forEach((target, index) => {
      const id = chatTargetVertexId(target.targetId);
      const vertex: FocusVertex = {
        id,
        focusId: CHAT_FOCUS,
        kind: 'chatTarget',
        rect: chatRect,
        orderKey: target.orderKey ?? index,
        chatTargetId: target.targetId,
      };
      vertices.set(id, vertex);
      chatTargetVertexIds.push(id);
      if (target.targetId === activeTargetId) {
        activeChatTargetVertexId = id;
      }
    });
    if (activeChatTargetVertexId === null) {
      activeChatTargetVertexId = chatTargetVertexIds[0] ?? null;
    }
  }

  const sortedPaneVertexIds = sortedVertexIds(vertices, paneVertexIds);
  const sortedChatTargetVertexIds = sortedVertexIds(vertices, chatTargetVertexIds);
  const edges: FocusEdge[] = [];
  const edgeSources = [...sortedPaneVertexIds];
  if (activeChatTargetVertexId !== null) {
    edgeSources.push(activeChatTargetVertexId);
  }
  for (const from of edgeSources) {
    for (const direction of ['left', 'right', 'up', 'down'] as const) {
      const edge = buildOrdinaryEdge(
        vertices,
        from,
        direction,
        activeChatTargetVertexId,
        sortedPaneVertexIds,
      );
      if (edge !== null) {
        edges.push(edge);
      }
    }
  }

  sortedChatTargetVertexIds.forEach((from, index) => {
    const count = sortedChatTargetVertexIds.length;
    if (count < 2) {
      return;
    }
    const left = sortedChatTargetVertexIds[(index - 1 + count) % count];
    const right = sortedChatTargetVertexIds[(index + 1) % count];
    if (left !== undefined) {
      edges.push({ from, to: left, direction: 'left', traversal: 'syntheticChatTarget' });
    }
    if (right !== undefined) {
      edges.push({ from, to: right, direction: 'right', traversal: 'syntheticChatTarget' });
    }
  });

  return {
    vertices,
    edges,
    paneVertexIds: sortedPaneVertexIds,
    chatTargetVertexIds: sortedChatTargetVertexIds,
    activeChatTargetVertexId,
  };
}

export function resolveEffectiveFocus(intended: FocusId, graph: FocusGraph): FocusId {
  if (intended === CHAT_FOCUS) {
    return CHAT_FOCUS;
  }
  const vertex = graph.vertices.get(intended);
  return vertex?.kind === 'pane' ? intended : CHAT_FOCUS;
}

function sourceVertexIdForFocus(
  current: FocusId,
  graph: FocusGraph,
): FocusGraphTargetId | null {
  if (current === CHAT_FOCUS) {
    return graph.activeChatTargetVertexId;
  }
  return graph.vertices.has(current) ? current : graph.activeChatTargetVertexId;
}

function edgeForDirection(
  graph: FocusGraph,
  source: FocusGraphTargetId,
  direction: Direction,
): FocusEdge | null {
  const sourceVertex = graph.vertices.get(source);
  const matches = graph.edges.filter(
    (candidate) => candidate.from === source && candidate.direction === direction,
  );
  if (sourceVertex?.kind === 'chatTarget' && (direction === 'left' || direction === 'right')) {
    return (
      matches.find((candidate) => candidate.traversal === 'syntheticChatTarget') ??
      matches[0] ??
      null
    );
  }
  return matches.find((candidate) => candidate.traversal === 'ordinaryPaneAdjacency') ?? null;
}

export function navigateFocus(
  graph: FocusGraph,
  current: FocusId,
  direction: Direction,
  state: FocusGraphState = { activeChatTargetId: null, lastTargetByDirection: {} },
): FocusNavigationResult {
  const source = sourceVertexIdForFocus(current, graph);
  const edge = source === null ? null : edgeForDirection(graph, source, direction);
  const targetVertex = edge === null ? null : (graph.vertices.get(edge.to) ?? null);
  const targetId = targetVertex?.id ?? null;
  const chatTargetId =
    targetVertex?.kind === 'chatTarget'
      ? (targetVertex.chatTargetId ?? targetIdForVirtualVertex(targetVertex.id))
      : null;
  return {
    targetId,
    focusId: targetVertex === null ? null : focusIdForVertex(targetVertex),
    chatTargetId,
    edge,
    state: {
      activeChatTargetId: chatTargetId ?? state.activeChatTargetId,
      lastTargetByDirection:
        targetId === null
          ? state.lastTargetByDirection
          : { ...state.lastTargetByDirection, [direction]: targetId },
    },
  };
}
