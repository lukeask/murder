import { describe, expect, it } from 'vitest';
import { layoutWorkflow, NODE_WIDTH } from '@murder/ui-core/workflowEditor/layout.js';
import type { EditorWorkflow } from '@murder/ui-core/workflowEditor/model.js';

const stage = (key: string, dependsOn: readonly string[] = []) => ({
  key,
  id: key,
  title: key,
  instructions: '',
  harness: 'codex',
  model: 'o3',
  worktree: null,
  dependsOn,
  gate: 'auto' as const,
});
const make = (): EditorWorkflow => ({
  name: 'flow',
  description: '',
  mode: 'static',
  stages: [
    stage('root'),
    stage('middle', ['root']),
    stage('leaf', ['middle']),
    stage('also', ['root']),
  ],
});

describe('layoutWorkflow', () => {
  it('uses longest dependency paths as deterministic ranks', () => {
    const layout = layoutWorkflow(make());
    expect(layout.nodes.get('root')?.rank).toBe(0);
    expect(layout.nodes.get('middle')?.rank).toBe(1);
    expect(layout.nodes.get('also')?.rank).toBe(1);
    expect(layout.nodes.get('leaf')?.rank).toBe(2);
    expect(layout.nodes.get('middle')?.rect.x).toBeLessThan(layout.nodes.get('leaf')?.rect.x ?? 0);
    expect(layout.bounds.width).toBeGreaterThan(NODE_WIDTH);
    expect(layoutWorkflow(make())).toEqual(layout);
  });

  it('lays cyclic members together instead of failing', () => {
    const layout = layoutWorkflow({ ...make(), stages: [stage('a', ['b']), stage('b', ['a'])] });
    expect(layout.nodes.get('a')?.rank).toBe(layout.nodes.get('b')?.rank);
  });
});
