import { describe, expect, it } from 'vitest';
import type { EditorWorkflow } from '../../src/workflowEditor/model.js';
import { collectPlaceholders } from '../../src/workflowEditor/placeholders.js';

describe('collectPlaceholders', () => {
  it('uses title/instruction definition order and first appearance deduplication', () => {
    const workflow: EditorWorkflow = {
      name: 'f',
      description: '',
      mode: 'static',
      stages: [
        {
          key: 'a',
          id: 'a',
          title: '{repo} {branch}',
          instructions: '{repo} {ticket-id}',
          harness: null,
          model: null,
          worktree: null,
          dependsOn: [],
          gate: 'auto',
        },
        {
          key: 'b',
          id: 'b',
          title: '{owner}',
          instructions: '{branch}',
          harness: null,
          model: null,
          worktree: null,
          dependsOn: [],
          gate: 'auto',
        },
      ],
    };
    expect(collectPlaceholders(workflow)).toEqual(['repo', 'branch', 'ticket-id', 'owner']);
  });
});
