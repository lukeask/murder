import { describe, expect, it } from 'vitest';
import { layoutWorkflow } from '@murder/ui-core/workflowEditor/layout.js';
import type { EditorWorkflow } from '@murder/ui-core/workflowEditor/model.js';
import { autoPan, nearestNode } from '@murder/ui-core/workflowEditor/navigation.js';

const workflow: EditorWorkflow = {
  name: 'f',
  description: '',
  mode: 'static',
  stages: [
    {
      key: 'a',
      id: 'a',
      title: '',
      instructions: '',
      harness: 'h',
      model: 'm',
      worktree: null,
      dependsOn: [],
      gate: 'auto',
    },
    {
      key: 'b',
      id: 'b',
      title: '',
      instructions: '',
      harness: 'h',
      model: 'm',
      worktree: null,
      dependsOn: ['a'],
      gate: 'auto',
    },
  ],
};

describe('navigation', () => {
  it('reveals a selected node with the smallest required viewport movement', () => {
    expect(autoPan({ x: 0, y: 0 }, { x: 20, y: 9, width: 8, height: 5 }, 20, 10)).toEqual({
      x: 8,
      y: 4,
    });
  });
  it('chooses direct dependency and dependent neighbors', () => {
    const layout = layoutWorkflow(workflow);
    expect(nearestNode(layout, 'b', 'dependency')).toBe('a');
    expect(nearestNode(layout, 'a', 'dependent')).toBe('b');
  });
  it('falls back to the nearest node in the adjacent rank', () => {
    const disconnected: EditorWorkflow = {
      ...workflow,
      stages: [
        ...workflow.stages,
        {
          key: 'lonely',
          id: 'lonely',
          title: '',
          instructions: '',
          harness: 'h',
          model: 'm',
          worktree: null,
          dependsOn: [],
          gate: 'auto',
        },
      ],
    };

    expect(nearestNode(layoutWorkflow(disconnected), 'lonely', 'dependent')).toBe('b');
  });
});
