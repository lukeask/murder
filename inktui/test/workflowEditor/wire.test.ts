import { describe, expect, it } from 'vitest';
import { fromWire, toWire, type WorkflowWire } from '../../src/workflowEditor/wire.js';

describe('workflow wire normalization', () => {
  it('normalizes optional protocol fields but preserves server definition version on round trip', () => {
    const wire: WorkflowWire = {
      name: 'flow',
      definition_version: 4,
      stages: [{ id: 'one', title: 'One' }],
    };
    const editor = fromWire(wire);
    expect(editor).toMatchObject({
      name: 'flow',
      definitionVersion: 4,
      description: '',
      mode: 'static',
    });
    expect(editor.stages[0]).toMatchObject({
      id: 'one',
      instructions: '',
      harness: null,
      model: null,
      worktree: null,
      dependsOn: [],
      gate: 'auto',
    });
    expect(toWire(editor)).toEqual({
      name: 'flow',
      definition_version: 4,
      description: '',
      mode: 'static',
      stages: [
        {
          id: 'one',
          title: 'One',
          instructions: '',
          harness: null,
          model: null,
          worktree: null,
          depends_on: [],
          gate: 'auto',
        },
      ],
    });
  });
});
