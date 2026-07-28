import { describe, expect, it } from 'vitest';
import type { EditorWorkflow } from '../../src/workflowEditor/model.js';
import {
  dependencyLegality,
  initialEditorState,
  reduceEditor,
} from '../../src/workflowEditor/reducer.js';

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
const flow = (): EditorWorkflow => ({
  name: 'flow',
  description: '',
  mode: 'static',
  stages: [stage('a', 'a'), stage('b', 'b', ['a']), stage('c', 'c', ['b'])],
});

describe('editor reducer', () => {
  it('renames atomically and undo/redo retains one snapshot per committed operation', () => {
    const initial = initialEditorState(flow());
    const renamed = reduceEditor(initial, {
      type: 'edit',
      edit: { type: 'rename-stage', key: 'a', id: 'root' },
    });
    expect(renamed.draft.stages[1]?.dependsOn).toEqual(['root']);
    expect(renamed.undo).toHaveLength(1);
    expect(reduceEditor(renamed, { type: 'undo' }).draft.stages[1]?.dependsOn).toEqual(['a']);
    expect(
      reduceEditor(reduceEditor(renamed, { type: 'undo' }), { type: 'redo' }).draft.stages[1]
        ?.dependsOn,
    ).toEqual(['root']);
  });

  it('routes an ID field commit through the same atomic rename operation', () => {
    const renamed = reduceEditor(initialEditorState(flow()), {
      type: 'edit',
      edit: { type: 'set-field', key: 'a', field: 'id', value: 'root' },
    });

    expect(renamed.draft.stages[0]?.id).toBe('root');
    expect(renamed.draft.stages[1]?.dependsOn).toEqual(['root']);
    expect(renamed.undo).toHaveLength(1);
  });

  it('commits a whole text field as one history entry and keeps history across save', () => {
    const initial = initialEditorState(flow());
    const edited = reduceEditor(initial, {
      type: 'edit',
      edit: { type: 'set-field', key: 'b', field: 'instructions', value: 'many typed chars' },
    });
    expect(edited.undo).toHaveLength(1);

    const saved = reduceEditor(edited, { type: 'saved', workflow: edited.draft });
    expect(saved.base).toBe(edited.draft);
    const undone = reduceEditor(saved, { type: 'undo' });
    expect(undone.draft.stages[1]?.instructions).toBe('');
    expect(undone.base.stages[1]?.instructions).toBe('many typed chars');
    expect(undone.draft).not.toEqual(undone.base);
  });

  it('refuses an ambiguous duplicate-ID rename and deletes without splicing', () => {
    const duplicate: EditorWorkflow = {
      ...flow(),
      stages: [stage('a', 'same'), stage('b', 'same'), stage('c', 'c', ['same'])],
    };
    expect(
      reduceEditor(initialEditorState(duplicate), {
        type: 'edit',
        edit: { type: 'rename-stage', key: 'a', id: 'new' },
      }),
    ).toEqual(initialEditorState(duplicate));
    const deleted = reduceEditor(initialEditorState(flow()), {
      type: 'edit',
      edit: { type: 'delete-stage', key: 'b' },
    });
    expect(deleted.draft.stages.map((item) => item.id)).toEqual(['a', 'c']);
    expect(deleted.draft.stages[1]?.dependsOn).toEqual([]);
  });

  it('only allows dependency additions that do not close a cycle', () => {
    expect(dependencyLegality(flow(), 'a', 'c')).toBe('cycle');
    expect(dependencyLegality(flow(), 'b', 'a')).toBe('remove');
    const added = reduceEditor(initialEditorState(flow()), {
      type: 'edit',
      edit: { type: 'toggle-dependency', target: 'b', source: 'c' },
    });
    expect(added).toEqual(initialEditorState(flow()));
  });

  it('does not mutate duplicate dependency references marked invalid', () => {
    const duplicateDependency: EditorWorkflow = {
      ...flow(),
      stages: [stage('a', 'a'), stage('b', 'b', ['a', 'a'])],
    };
    const initial = initialEditorState(duplicateDependency);

    expect(dependencyLegality(duplicateDependency, 'b', 'a')).toBe('invalid');
    expect(
      reduceEditor(initial, {
        type: 'edit',
        edit: { type: 'toggle-dependency', target: 'b', source: 'a' },
      }),
    ).toBe(initial);
  });
});
