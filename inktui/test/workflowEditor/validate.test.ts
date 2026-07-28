import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import type { EditorWorkflow } from '../../src/workflowEditor/model.js';
import { validateEditorWorkflow } from '../../src/workflowEditor/validate.js';
import { fromWire, type WorkflowWire } from '../../src/workflowEditor/wire.js';

describe('validateEditorWorkflow', () => {
  it('reports independently repairable malformed graph facts without throwing', () => {
    const workflow: EditorWorkflow = {
      name: 'bad name',
      description: '',
      mode: 'generative',
      stages: [
        {
          key: 'a',
          id: '',
          title: '',
          instructions: '',
          harness: null,
          model: null,
          worktree: null,
          dependsOn: ['', 'missing', 'missing'],
          gate: 'human',
        },
        {
          key: 'b',
          id: '',
          title: '',
          instructions: '',
          harness: null,
          model: null,
          worktree: null,
          dependsOn: [],
          gate: 'auto',
        },
      ],
    };
    const codes = validateEditorWorkflow(workflow).map((issue) => issue.code);
    expect(codes).toContain('invalid_name');
    expect(codes).toContain('invalid_stage_id');
    expect(codes).toContain('duplicate_stage_id');
    expect(codes).toContain('missing_harness');
    expect(codes).toContain('missing_model');
    expect(codes).toContain('duplicate_dependency');
    expect(codes).toContain('ambiguous_dependency');
    expect(codes).toContain('unknown_dependency');
    expect(codes).toContain('unsupported_mode');
    expect(codes).toContain('unsupported_gate');
  });

  it('matches the shared backend validation issue-code corpus', () => {
    const fixturePath = resolve(
      import.meta.dirname,
      '../../../tests/fixtures/workflows/validation_cases.json',
    );
    const cases = JSON.parse(readFileSync(fixturePath, 'utf8')) as readonly {
      readonly name: string;
      readonly workflow: WorkflowWire;
      readonly issue_codes: readonly string[];
    }[];

    for (const testCase of cases) {
      expect(
        [
          ...new Set(
            validateEditorWorkflow(fromWire(testCase.workflow)).map((issue) => issue.code),
          ),
        ].sort(),
        testCase.name,
      ).toEqual([...testCase.issue_codes].sort());
    }
  });
});
