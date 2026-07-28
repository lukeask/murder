import type { EditorIssue, EditorWorkflow, StageKey } from './model.js';

export interface GraphNode {
  readonly key: StageKey;
  readonly id: string;
  readonly index: number;
}
export interface ResolvedEdge {
  readonly source: StageKey;
  readonly target: StageKey;
  readonly dependency: string;
  readonly dependencyIndex: number;
}
export interface UnresolvedEdge {
  readonly target: StageKey;
  readonly dependency: string;
  readonly dependencyIndex: number;
  readonly kind: 'dangling' | 'ambiguous';
}
export interface StrongComponent {
  readonly key: string;
  readonly members: readonly StageKey[];
  readonly cyclic: boolean;
}
export interface EditorGraph {
  readonly workflow: EditorWorkflow;
  readonly nodes: ReadonlyMap<StageKey, GraphNode>;
  readonly resolvedEdges: readonly ResolvedEdge[];
  readonly unresolvedEdges: readonly UnresolvedEdge[];
  readonly incoming: ReadonlyMap<StageKey, readonly ResolvedEdge[]>;
  readonly outgoing: ReadonlyMap<StageKey, readonly ResolvedEdge[]>;
  readonly components: readonly StrongComponent[];
  readonly componentByNode: ReadonlyMap<StageKey, StrongComponent>;
  readonly issuesByNode: ReadonlyMap<StageKey, readonly EditorIssue[]>;
}

function push<T>(map: Map<StageKey, T[]>, key: StageKey, value: T): void {
  const values = map.get(key) ?? [];
  values.push(value);
  map.set(key, values);
}

/** Builds a useful graph even for drafts which the server rightly refuses to save. */
export function buildEditorGraph(workflow: EditorWorkflow): EditorGraph {
  const nodes = new Map<StageKey, GraphNode>();
  const ids = new Map<string, StageKey[]>();
  const incoming = new Map<StageKey, ResolvedEdge[]>();
  const outgoing = new Map<StageKey, ResolvedEdge[]>();
  const issues = new Map<StageKey, EditorIssue[]>();
  const addIssue = (key: StageKey, issue: EditorIssue): void => push(issues, key, issue);
  workflow.stages.forEach((stage, index) => {
    nodes.set(stage.key, { key: stage.key, id: stage.id, index });
    const keys = ids.get(stage.id) ?? [];
    keys.push(stage.key);
    ids.set(stage.id, keys);
  });
  for (const [id, keys] of ids)
    if (keys.length > 1) {
      for (const key of keys)
        addIssue(key, {
          code: 'duplicate_stage_id',
          severity: 'error',
          message: `Duplicate stage ID “${id}”.`,
          stageKey: key,
          field: 'id',
        });
    }
  const resolvedEdges: ResolvedEdge[] = [];
  const unresolvedEdges: UnresolvedEdge[] = [];
  for (const stage of workflow.stages)
    stage.dependsOn.forEach((dependency, dependencyIndex) => {
      const sources = ids.get(dependency) ?? [];
      if (sources.length === 1) {
        const source = sources[0] as StageKey;
        const edge = { source, target: stage.key, dependency, dependencyIndex };
        resolvedEdges.push(edge);
        push(outgoing, source, edge);
        push(incoming, stage.key, edge);
      } else {
        const kind = sources.length === 0 ? 'dangling' : 'ambiguous';
        const edge = { target: stage.key, dependency, dependencyIndex, kind } as const;
        unresolvedEdges.push(edge);
        addIssue(stage.key, {
          code: kind === 'dangling' ? 'unknown_dependency' : 'ambiguous_dependency',
          severity: 'error',
          message: `${kind === 'dangling' ? 'Unknown' : 'Ambiguous'} dependency “${dependency}”.`,
          stageKey: stage.key,
          dependencyIndex,
        });
      }
    });
  const components = tarjan(nodes, outgoing);
  const componentByNode = new Map<StageKey, StrongComponent>();
  for (const component of components) {
    for (const key of component.members) componentByNode.set(key, component);
    if (component.cyclic)
      for (const key of component.members)
        addIssue(key, {
          code: 'cycle',
          severity: 'error',
          message: 'Stage is part of a dependency cycle.',
          stageKey: key,
        });
  }
  return {
    workflow,
    nodes,
    resolvedEdges,
    unresolvedEdges,
    incoming,
    outgoing,
    components,
    componentByNode,
    issuesByNode: issues,
  };
}

function tarjan(
  nodes: ReadonlyMap<StageKey, GraphNode>,
  outgoing: ReadonlyMap<StageKey, readonly ResolvedEdge[]>,
): StrongComponent[] {
  let index = 0;
  const indices = new Map<StageKey, number>();
  const low = new Map<StageKey, number>();
  const stack: StageKey[] = [];
  const onStack = new Set<StageKey>();
  const result: StrongComponent[] = [];
  const visit = (key: StageKey): void => {
    indices.set(key, index);
    low.set(key, index);
    index += 1;
    stack.push(key);
    onStack.add(key);
    for (const edge of outgoing.get(key) ?? []) {
      if (!indices.has(edge.target)) {
        visit(edge.target);
        low.set(key, Math.min(low.get(key) as number, low.get(edge.target) as number));
      } else if (onStack.has(edge.target))
        low.set(key, Math.min(low.get(key) as number, indices.get(edge.target) as number));
    }
    if (low.get(key) === indices.get(key)) {
      const members: StageKey[] = [];
      let member: StageKey | undefined;
      do {
        member = stack.pop();
        if (member !== undefined) {
          onStack.delete(member);
          members.push(member);
        }
      } while (member !== key);
      members.sort(
        (a, b) =>
          (nodes.get(a) as GraphNode).index - (nodes.get(b) as GraphNode).index ||
          a.localeCompare(b),
      );
      const selfLoop = (outgoing.get(key) ?? []).some((edge) => edge.target === key);
      result.push({ key: `scc:${result.length}`, members, cyclic: members.length > 1 || selfLoop });
    }
  };
  for (const key of nodes.keys()) if (!indices.has(key)) visit(key);
  return result;
}

/** True when following dependency -> dependent edges reaches `to`. */
export function reachable(graph: EditorGraph, from: StageKey, to: StageKey): boolean {
  const seen = new Set<StageKey>();
  const todo = [from];
  while (todo.length > 0) {
    const key = todo.pop() as StageKey;
    if (key === to) return true;
    if (!seen.has(key)) {
      seen.add(key);
      for (const edge of graph.outgoing.get(key) ?? []) todo.push(edge.target);
    }
  }
  return false;
}
