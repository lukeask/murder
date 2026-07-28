import { describe, expect, it } from 'vitest';
import { buildEditorGraph, reachable } from '../../src/workflowEditor/graph.js';
import type { EditorWorkflow } from '../../src/workflowEditor/model.js';

const workflow = (stages: EditorWorkflow['stages']): EditorWorkflow => ({
  name: 'flow',
  description: '',
  mode: 'static',
  stages,
});
const stage = (key: string, id: string, dependsOn: readonly string[] = []) => ({
  key,
  id,
  title: id,
  instructions: '',
  harness: 'codex',
  model: 'o3',
  worktree: null,
  dependsOn,
  gate: 'auto' as const,
});

describe('buildEditorGraph', () => {
  it('keeps duplicate IDs separately selectable and makes dependent references ambiguous', () => {
    const graph = buildEditorGraph(
      workflow([stage('one', 'same'), stage('two', 'same'), stage('target', 'target', ['same'])]),
    );
    expect([...graph.nodes.keys()]).toEqual(['one', 'two', 'target']);
    expect(graph.resolvedEdges).toEqual([]);
    expect(graph.unresolvedEdges).toEqual([
      { target: 'target', dependency: 'same', dependencyIndex: 0, kind: 'ambiguous' },
    ]);
    expect(graph.issuesByNode.get('target')?.[0]?.code).toBe('ambiguous_dependency');
  });

  it('retains dangling edges and identifies strongly connected components', () => {
    const graph = buildEditorGraph(
      workflow([stage('a', 'a', ['b']), stage('b', 'b', ['a']), stage('c', 'c', ['gone'])]),
    );
    expect(graph.unresolvedEdges[0]?.kind).toBe('dangling');
    expect(
      graph.components.some(
        (component) => component.cyclic && component.members.join(',') === 'a,b',
      ),
    ).toBe(true);
    expect(reachable(graph, 'a', 'b')).toBe(true);
  });
});
