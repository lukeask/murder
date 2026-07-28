import { describe, expect, it } from 'vitest';
import { compileWorkflowTemplate } from '../../src/workflowEditor/compile.js';
import type { EditorWorkflow } from '../../src/workflowEditor/model.js';
import { collectPlaceholders } from '../../src/workflowEditor/placeholders.js';

function stage(
  key: string,
  title: string,
  instructions: string,
): EditorWorkflow['stages'][number] {
  return {
    key,
    id: key,
    title,
    instructions,
    harness: null,
    model: null,
    worktree: null,
    dependsOn: [],
    gate: 'auto',
  };
}

describe('collectPlaceholders', () => {
  it('uses title/instruction definition order and first appearance deduplication', () => {
    const workflow: EditorWorkflow = {
      name: 'f',
      description: '',
      mode: 'static',
      stages: [
        stage('a', '{repo} {branch}', '{repo} {ticket-id}'),
        stage('b', '{owner}', '{branch}'),
      ],
    };
    expect(collectPlaceholders(workflow)).toEqual(['repo', 'branch', 'ticket-id', 'owner']);
  });
});

describe('compileWorkflowTemplate', () => {
  it('collects placeholders from expanded prompt templates (after :name: expansion)', () => {
    const templates = new Map<string, string>([
      ['review-context', 'Review {subject}.\nCheck specifically for {risk_area}.'],
    ]);
    const workflow: EditorWorkflow = {
      name: 'review',
      description: '',
      mode: 'static',
      stages: [stage('review', 'Review', ':review-context:\n\nReturn a prioritized report.')],
    };

    const compiled = compileWorkflowTemplate(workflow, templates);
    expect(compiled.issues).toEqual([]);
    expect(compiled.placeholders).toEqual(['subject', 'risk_area']);
    expect(compiled.fields.map((field) => field.name)).toEqual(['subject', 'risk_area']);
    expect(compiled.expanded.stages[0]?.instructions).toContain('Review {subject}.');
    expect(compiled.expanded.stages[0]?.instructions).not.toContain(':review-context:');
  });

  it('preserves wizard field order: stage order, title before instructions, first occurrence', () => {
    const templates = new Map<string, string>([
      ['intro', 'Hello {who}'],
      ['outro', 'Bye {who} and {place}'],
    ]);
    const workflow: EditorWorkflow = {
      name: 'ordered',
      description: '',
      mode: 'static',
      stages: [
        stage('a', '{alpha} :intro:', '{beta}'),
        stage('b', 'done', ':outro: {gamma}'),
      ],
    };

    const compiled = compileWorkflowTemplate(workflow, templates);
    expect(compiled.issues).toEqual([]);
    // a.title: alpha, who (from :intro:); a.instructions: beta; b.instructions: who(already), place, gamma
    expect(compiled.fields.map((field) => field.name)).toEqual([
      'alpha',
      'who',
      'beta',
      'place',
      'gamma',
    ]);
  });

  it('surfaces unknown :template: refs as compile errors', () => {
    const workflow: EditorWorkflow = {
      name: 'broken',
      description: '',
      mode: 'static',
      stages: [stage('work', 'T', 'Use :missing-template: here {ok}')],
    };

    const compiled = compileWorkflowTemplate(workflow, new Map());
    expect(compiled.issues).toEqual([
      expect.objectContaining({
        code: 'unknown_prompt_template',
        templateName: 'missing-template',
        field: 'instructions',
        stageKey: 'work',
      }),
    ]);
    // Placeholders after expansion still collected (unknown left verbatim, so {ok} remains).
    expect(compiled.placeholders).toEqual(['ok']);
  });

  it('uses declared input labels/defaults when provided', () => {
    const workflow: EditorWorkflow = {
      name: 'labeled',
      description: '',
      mode: 'static',
      stages: [stage('work', '{subject}', '{risk_area}')],
    };

    const compiled = compileWorkflowTemplate(workflow, new Map(), {
      declaredInputs: {
        subject: { label: 'What should be reviewed?', kind: 'text', required: true },
        risk_area: { label: 'Particular risk area', default: 'correctness', kind: 'text' },
      },
    });

    expect(compiled.fields).toEqual([
      {
        name: 'subject',
        label: 'What should be reviewed?',
        kind: 'text',
        required: true,
        defaultValue: '',
      },
      {
        name: 'risk_area',
        label: 'Particular risk area',
        kind: 'text',
        required: false,
        defaultValue: 'correctness',
      },
    ]);
  });

  it('infers plain text fields when no declarations exist', () => {
    const workflow: EditorWorkflow = {
      name: 'plain',
      description: '',
      mode: 'static',
      stages: [stage('work', '{target}', 'Deploy {target} to {region}')],
    };
    const compiled = compileWorkflowTemplate(workflow, new Map());
    expect(compiled.fields).toEqual([
      {
        name: 'target',
        label: 'target',
        kind: 'text',
        required: false,
        defaultValue: '',
      },
      {
        name: 'region',
        label: 'region',
        kind: 'text',
        required: false,
        defaultValue: '',
      },
    ]);
  });
});
