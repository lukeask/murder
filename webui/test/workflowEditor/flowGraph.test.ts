/**
 * Pure flowGraph / layout mapping tests — prefer these over mounting React Flow in jsdom.
 */

import { describe, expect, it } from 'vitest';
import type { EditorWorkflow } from '@murder/ui-core/workflowEditor/model.js';
import { applyWorkflowEdit, dependencyLegality } from '@murder/ui-core/workflowEditor/reducer.js';
import { createLocalStageKey } from '@murder/ui-core/workflowEditor/wire.js';
import {
  parseDependencyEdgeId,
  workflowToFlow,
} from '../../src/components/workflowEditor/flowGraph.js';
import { mergePositions, seedPositions } from '../../src/components/workflowEditor/layout.js';

function stage(
  id: string,
  dependsOn: readonly string[] = [],
): EditorWorkflow['stages'][number] {
  return {
    key: createLocalStageKey(),
    id,
    title: id,
    instructions: '',
    harness: 'claude_code',
    model: 'sonnet',
    worktree: null,
    dependsOn,
    gate: 'auto',
  };
}

function workflow(stages: EditorWorkflow['stages']): EditorWorkflow {
  return { name: 'demo', description: '', mode: 'static', stages };
}

describe('seedPositions / mergePositions', () => {
  it('places dependents to the right of dependencies by rank', () => {
    const a = stage('a');
    const b = stage('b', ['a']);
    const draft = workflow([a, b]);
    const positions = seedPositions(draft);
    expect(positions.get(a.key)?.x).toBeLessThan(positions.get(b.key)?.x ?? 0);
  });

  it('preserves existing positions when merging newcomers', () => {
    const a = stage('a');
    const draft = workflow([a]);
    const previous = seedPositions(draft);
    const kept = previous.get(a.key)!;
    const moved = new Map(previous);
    moved.set(a.key, { x: 999, y: 777 });

    const b = stage('b', ['a']);
    const nextDraft = workflow([a, b]);
    const merged = mergePositions(nextDraft, moved);
    expect(merged.get(a.key)).toEqual({ x: 999, y: 777 });
    expect(merged.get(b.key)).toBeDefined();
    expect(merged.get(b.key)).not.toEqual(kept);
  });

  it('relayout replaces all positions', () => {
    const a = stage('a');
    const draft = workflow([a]);
    const previous = new Map([[a.key, { x: 1, y: 2 }]]);
    const relaid = mergePositions(draft, previous, { relayout: true });
    expect(relaid.get(a.key)).not.toEqual({ x: 1, y: 2 });
  });
});

describe('workflowToFlow + dependency edits', () => {
  it('projects resolved dependsOn as source→target edges', () => {
    const a = stage('a');
    const b = stage('b', ['a']);
    const draft = workflow([a, b]);
    const { nodes, edges } = workflowToFlow(draft, seedPositions(draft));
    expect(nodes).toHaveLength(2);
    expect(edges).toHaveLength(1);
    expect(edges[0]?.source).toBe(a.key);
    expect(edges[0]?.target).toBe(b.key);
    expect(parseDependencyEdgeId(edges[0]!.id)).toEqual({ source: a.key, target: b.key });
  });

  it('connect gesture maps to toggle-dependency add when legal', () => {
    const a = stage('a');
    const b = stage('b');
    const draft = workflow([a, b]);
    expect(dependencyLegality(draft, b.key, a.key)).toBe('add');
    const next = applyWorkflowEdit(draft, {
      type: 'toggle-dependency',
      target: b.key,
      source: a.key,
    });
    expect(next.stages.find((s) => s.key === b.key)?.dependsOn).toEqual(['a']);
    const { edges } = workflowToFlow(next, seedPositions(next));
    expect(edges).toHaveLength(1);
  });

  it('edge delete maps to toggle-dependency remove', () => {
    const a = stage('a');
    const b = stage('b', ['a']);
    const draft = workflow([a, b]);
    expect(dependencyLegality(draft, b.key, a.key)).toBe('remove');
    const next = applyWorkflowEdit(draft, {
      type: 'toggle-dependency',
      target: b.key,
      source: a.key,
    });
    expect(next.stages.find((s) => s.key === b.key)?.dependsOn).toEqual([]);
    expect(workflowToFlow(next, seedPositions(next)).edges).toHaveLength(0);
  });

  it('rejects cyclic connect via dependencyLegality', () => {
    const a = stage('a');
    const b = stage('b', ['a']);
    const draft = workflow([a, b]);
    // b already depends on a; adding a→b (a depends on b) would cycle.
    expect(dependencyLegality(draft, a.key, b.key)).toBe('cycle');
  });

  it('surfaces stage validation issues on node data', () => {
    const bare = stage('bare');
    const draft = workflow([
      { ...bare, harness: null, model: null },
    ]);
    const { nodes } = workflowToFlow(draft, seedPositions(draft));
    expect(nodes[0]?.data.hasError).toBe(true);
    expect(nodes[0]?.data.issueCount).toBeGreaterThan(0);
  });

  it('attaches runStatus from stageStatuses keyed by stage id', () => {
    const a = stage('build');
    const draft = workflow([a]);
    const stageStatuses = new Map([['build', 'running' as const]]);
    const { nodes } = workflowToFlow(draft, seedPositions(draft), { stageStatuses });
    expect(nodes[0]?.data.runStatus).toBe('running');
  });
});
